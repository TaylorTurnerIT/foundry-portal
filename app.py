import os
import yaml
import re
import json
import shutil
import docker
import time
from urllib.parse import urlparse
from functools import wraps
from flask import Flask, render_template, jsonify, request, session
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from werkzeug.security import generate_password_hash, check_password_hash

# Initialize the Flask application
app = Flask(__name__)
app.secret_key = os.urandom(24)

# Global cache to store instance data
instance_data_cache = []
CONFIG_FILE = 'config.yaml'

# --- DOMAIN CONFIGURATION ---
# Get domain from environment variable, default to localhost
FOUNDRY_DOMAIN = os.environ.get('FOUNDRY_DOMAIN', 'localhost')
# Construct default public host URL based on domain
# Use https for non-localhost domains, http for localhost
DEFAULT_PUBLIC_HOST = f"https://{FOUNDRY_DOMAIN}" if FOUNDRY_DOMAIN != 'localhost' else "http://localhost"

# -----------------------------------------------------------------------------
# CONFIG HELPERS
# -----------------------------------------------------------------------------
def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, 'r') as file:
        return yaml.safe_load(file) or {}

def save_config(config):
    with open(CONFIG_FILE, 'w') as file:
        yaml.dump(config, file)

# --- DYNAMIC DATA DIRECTORY ---
if os.environ.get('FOUNDRY_DATA_DIR'):
    DATA_DIR = os.environ.get('FOUNDRY_DATA_DIR')
elif os.path.exists('/data/foundry'):
    DATA_DIR = '/data/foundry'
else:
    # Local dev fallback
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'foundry-data')
    if not os.path.exists(DATA_DIR):
        print(f"DEBUG: DEV MODE - Creating local data directory at: {DATA_DIR}")
        os.makedirs(DATA_DIR, exist_ok=True)

REGISTRY_FILE = os.path.join(DATA_DIR, 'instances.json')
TEMPLATES_DIR = os.path.join(DATA_DIR, 'templates')
INSTANCES_DIR = os.path.join(DATA_DIR, 'instances') 
CENTRAL_WORLDS_DIR = os.path.join(DATA_DIR, 'worlds') # <--- NEW CENTRAL FOLDER
CACHE_DIR = os.path.join(DATA_DIR, 'cache')
CADDY_ROUTES_FILE = '/data/routes.caddy'
DOCKER_NETWORK = 'foundry_net'

os.makedirs(INSTANCES_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(CENTRAL_WORLDS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# -----------------------------------------------------------------------------
# ORCHESTRATOR CLASS
# -----------------------------------------------------------------------------
class Orchestrator:
    def __init__(self):
        try:
            self.client = docker.from_env()
            print("DEBUG ORCHESTRATOR: Docker client connected.")
        except Exception as e:
            print(f"DEBUG ORCHESTRATOR: Failed to connect to Docker socket: {e}")
            print("TIP: For Podman, ensure the socket is enabled and DOCKER_HOST is set.")
            self.client = None

    def load_registry(self):
        if not os.path.exists(REGISTRY_FILE): return {}
        try:
            with open(REGISTRY_FILE, 'r') as f: return json.load(f)
        except: return {}

    def save_registry(self, registry):
        with open(REGISTRY_FILE, 'w') as f: json.dump(registry, f, indent=2)

    def get_next_port(self):
        registry = self.load_registry()
        used_ports = {inst['port'] for inst in registry.values()}
        port = 30002
        while port in used_ports: port += 1
        return port

    def scan_for_new_worlds(self):
        """Scans CENTRAL_WORLDS_DIR. Registers new worlds AND updates versions for existing ones."""
        print("DEBUG: Scanning for new worlds...")
        if not os.path.exists(CENTRAL_WORLDS_DIR): return

        # Scan directories in foundry-data/worlds/
        for dirname in os.listdir(CENTRAL_WORLDS_DIR):
            dir_path = os.path.join(CENTRAL_WORLDS_DIR, dirname)
            if dirname.startswith('.'): continue
            
            if os.path.isdir(dir_path):
                # 1. Detect Version from world.json (Always do this)
                image_tag = "release" # Default
                world_json_path = os.path.join(dir_path, "world.json")
                
                if os.path.exists(world_json_path):
                    try:
                        with open(world_json_path, 'r') as f:
                            world_data = json.load(f)
                            if 'compatibility' in world_data and 'verified' in world_data['compatibility']:
                                image_tag = world_data['compatibility']['verified']
                            elif 'coreVersion' in world_data:
                                image_tag = world_data['coreVersion']
                    except Exception as e:
                        print(f"WARNING: Failed to read world.json for {dirname}: {e}")
                
                # Load registry fresh for every item to ensure port safety
                registry = self.load_registry()
                changes_made = False

                # 2. Register New World
                if dirname not in registry:
                    print(f"INFO: Discovered new world '{dirname}'. Auto-registering.")
                    
                    instance_path = os.path.join(INSTANCES_DIR, dirname)
                    os.makedirs(instance_path, exist_ok=True)
                    try: os.chmod(instance_path, 0o777)
                    except: pass

                    registry[dirname] = {
                        "name": dirname,
                        "port": self.get_next_port(), # Will now be accurate
                        "template": "Auto-Discovered",
                        "image_tag": image_tag, 
                        "created_at": time.time()
                    }
                    changes_made = True
                
                # 3. Update Existing World Version
                else:
                    current_tag = registry[dirname].get('image_tag')
                    # Update if tag is different OR if it was missing
                    if current_tag != image_tag:
                        print(f"INFO: Updating version for '{dirname}' from {current_tag} to {image_tag}")
                        registry[dirname]['image_tag'] = image_tag
                        changes_made = True

                # Save immediately to lock in the port/changes
                if changes_made:
                    self.save_registry(registry)

    def launch_instance(self, name, port):
        if not self.client:
            print(f"CRITICAL ERROR: Cannot launch {name}. Docker client is not connected.")
            return False

        game_name = f"foundry_{name}"
        nursery_name = f"nursery_{name}"
        
        # 1. Path to the Instance Configs (Logs, Config, etc.)
        instance_path = os.path.join(INSTANCES_DIR, name)
        
        # 2. Path to the ACTUAL World Data (The Central "Source of Truth")
        world_source_path = os.path.join(CENTRAL_WORLDS_DIR, name)
        
        # Ensure they exist
        os.makedirs(instance_path, exist_ok=True)
        os.makedirs(world_source_path, exist_ok=True)

        # --- NEW: Detect Host Socket Path ---
        host_socket_path = '/var/run/docker.sock'
        if os.environ.get('DOCKER_HOST', '').startswith('unix://'):
            host_socket_path = os.environ.get('DOCKER_HOST').replace('unix://', '')
        elif os.path.exists(f'/run/user/{os.getuid()}/podman/podman.sock'):
            host_socket_path = f'/run/user/{os.getuid()}/podman/podman.sock'
        
        # --- Generate Nursery Config ---
        nursery_conf_dir = os.path.join(instance_path, 'nursery_config')
        os.makedirs(nursery_conf_dir, exist_ok=True)
        try: os.chmod(nursery_conf_dir, 0o777)
        except: pass

        config = load_config()
        public_url = config.get('public_host', DEFAULT_PUBLIC_HOST)
        try: hostname = urlparse(public_url).hostname or FOUNDRY_DOMAIN
        except: hostname = FOUNDRY_DOMAIN
        
        nursery_config_data = {
            'proxyListeningPort': 80,
            'proxyHosts': [{
                'domain': list(set(['localhost', '127.0.0.1', hostname])),
                'containerName': game_name,
                'proxyHost': game_name,
                'proxyPort': 30000,
                'timeoutSeconds': 600,
                'displayName': name
            }]
        }

        config_path = os.path.join(nursery_conf_dir, 'config.yml')
        with open(config_path, 'w') as f:
            yaml.dump(nursery_config_data, f)
        try: os.chmod(config_path, 0o777)
        except: pass

        # Get Image Tag
        registry = self.load_registry()
        image_tag = registry.get(name, {}).get('image_tag', 'release')

        # --- Pre-Create Directory Structure ---
        # We must create 'Data/worlds' in the instance folder so Podman 
        # doesn't auto-create it with Root permissions when we mount the overlay.
        structure_path = os.path.join(instance_path, "Data", "worlds")
        os.makedirs(structure_path, exist_ok=True)
        try:
            # Recursively fix permissions from root of instance down
            for root, dirs, files in os.walk(instance_path):
                os.chmod(root, 0o777)
                for d in dirs: os.chmod(os.path.join(root, d), 0o777)
                for f in files: os.chmod(os.path.join(root, f), 0o777)
        except: pass

        # --- Environment Variables Construction ---
        env_vars = {
            "CONTAINER_CACHE": "/data/container_cache",
            "FOUNDRY_IP_DISCOVERY": "false",
            "FOUNDRY_WORLD": name,
            "FOUNDRY_HOSTNAME": FOUNDRY_DOMAIN,
            "FOUNDRY_ROUTE_PREFIX": name,
            "FOUNDRY_PROXY_SSL": "true",
            "FOUNDRY_PROXY_PORT": "443",
            "FOUNDRY_COMPRESS_WEBSOCKET": "true",
            "FOUNDRY_MINIFY_STATIC_FILES": "true"
        }
        
        # --- Load Secrets from JSON if available ---
        secrets_path = '/run/secrets/foundry_secrets.json'
        if os.path.exists(secrets_path):
            try:
                with open(secrets_path, 'r') as f:
                    secrets_data = json.load(f)
                    for k, v in secrets_data.items():
                        # Convert key to UPPERCASE to match container requirements
                        # e.g. "foundry_username" -> "FOUNDRY_USERNAME"
                        k_upper = k.upper()
                        
                        if k_upper.startswith('FOUNDRY_'):
                            env_vars[k_upper] = v
                            
                    print(f"DEBUG: Loaded secrets from {secrets_path}")
            except Exception as e:
                print(f"WARNING: Failed to load secrets from {secrets_path}: {e}")

        # 1. Explicitly pass Secrets and Critical Vars (Environment overrides file)
        explicit_pass_keys = [
            'FOUNDRY_USERNAME', 
            'FOUNDRY_PASSWORD', 
            'FOUNDRY_ADMIN_KEY', 
            'FOUNDRY_LICENSE_KEY'
        ]
        
        for key in explicit_pass_keys:
            if os.environ.get(key):
                env_vars[key] = os.environ.get(key)

        # 2. Dynamic Pass-through for Non-Secret Control
        # Allows you to set any FOUNDRY_ var in Nix (e.g. FOUNDRY_AWS_CONFIG) and have it passed down
        for k, v in os.environ.items():
            if k.startswith('FOUNDRY_') and k not in env_vars:
                env_vars[k] = v

        # 3. Launch Game with OVERLAY MOUNT
        try:
            try:
                c = self.client.containers.get(game_name)
                if c.status != 'running': c.start()
            except docker.errors.NotFound:
                print(f"DEBUG: Spawning {game_name} using tag {image_tag}...")
                
                volumes = {
                    # Base Data (Configs, Logs, Modules) -> /data
                    os.path.abspath(instance_path): {'bind': '/data', 'mode': 'z'},
                    
                    # Shared Cache -> /data/container_cache
                    os.path.abspath(CACHE_DIR): {'bind': '/data/container_cache', 'mode': 'z'},
                    
                    # SPECIFIC WORLD OVERLAY -> /data/Data/worlds/{name}
                    os.path.abspath(world_source_path): {'bind': f'/data/Data/worlds/{name}', 'mode': 'z'},

                    # Maps the portal's secret file to the game's secret file
                    '/run/secrets/foundry_secrets.json': {'bind': '/run/secrets/config.json', 'mode': 'ro'}
                }

                self.client.containers.run(
                    f"felddy/foundryvtt:{image_tag}",
                    name=game_name,
                    detach=True,
                    network=DOCKER_NETWORK,
                    volumes=volumes,
                    environment=env_vars,
                    ports={'30000/tcp': port}
                )
        except Exception as e:
            print(f"ERROR launching game {name}: {e}")
            return False

        # 4. Launch Nursery
        try:
            try:
                c = self.client.containers.get(nursery_name)
                if c.status != 'running': c.start()
            except docker.errors.NotFound:
                print(f"DEBUG: Spawning {nursery_name} on port {port}...")
                self.client.containers.run(
                    "ghcr.io/itsecholot/containernursery:1.9.0",
                    name=nursery_name,
                    detach=True,
                    network=DOCKER_NETWORK,
                    ports={'80/tcp': port},
                    user="0:0",
                    security_opt=["label=disable"],
                    volumes={
                        os.path.abspath(nursery_conf_dir): {'bind': '/usr/src/app/config', 'mode': 'z'},
                        host_socket_path: {'bind': '/var/run/docker.sock', 'mode': 'rw'}
                    }
                )
        except Exception as e:
            print(f"ERROR launching nursery {name}: {e}")
            return False
        return True

    def create_instance(self, name, source):
        if name in self.load_registry():
            raise ValueError("Instance name already exists.")
        
        # Create Instance Boilerplate (for Logs/Config)
        instance_root = os.path.join(INSTANCES_DIR, name)
        
        # Create Central World Data
        world_dest_dir = os.path.join(CENTRAL_WORLDS_DIR, name)
        
        # --- LOGIC: Template vs Empty ---
        if source.startswith("v"):
            # Empty Instance Logic
            version_tag = source[1:] 
            image_tag = version_tag
            template_used = None
            
            # Just create the empty folder
            os.makedirs(world_dest_dir, exist_ok=True)
            print(f"DEBUG: Created empty world {name} in central storage.")
            
        else:
            # Template Logic
            template_name = source
            src = os.path.join(TEMPLATES_DIR, template_name)
            if not os.path.exists(src):
                raise ValueError("Template not found.")
            
            image_tag = "release" 
            template_used = template_name
            
            # Copy Template directly to Central Worlds
            print(f"DEBUG: Copying template {src} to {world_dest_dir}")
            shutil.copytree(src, world_dest_dir)

        # Fix Permissions (Set to 777 for Container Access)
        try:
            # Fix Instance Root
            os.makedirs(instance_root, exist_ok=True)
            os.chmod(instance_root, 0o777)
            
            # Fix World Data (Recursive)
            for root, dirs, files in os.walk(world_dest_dir):
                os.chmod(root, 0o777)
                for d in dirs: os.chmod(os.path.join(root, d), 0o777)
                for f in files: os.chmod(os.path.join(root, f), 0o777)
                
            # Also fix Instance Root recursive just in case
            for root, dirs, files in os.walk(instance_root):
                os.chmod(root, 0o777)
                for d in dirs: os.chmod(os.path.join(root, d), 0o777)
        except Exception as e:
            print(f"WARNING: Failed to set permissions: {e}")

        # Register
        registry = self.load_registry()
        port = self.get_next_port()
        
        registry[name] = {
            "name": name,
            "port": port,
            "template": template_used,
            "image_tag": image_tag,
            "created_at": time.time()
        }
        self.save_registry(registry)
        
        if not self.launch_instance(name, port):
            raise Exception("Failed to launch containers.")
            
        self.update_caddy_routes()
        return port

    def reconcile(self):
        # 1. Auto-Discover new folders in 'worlds/'
        self.scan_for_new_worlds()
        
        # 2. Launch everything in registry
        print("DEBUG: Reconciling instances...")
        registry = self.load_registry()
        for name, data in registry.items():
            self.launch_instance(name, data['port'])
        self.update_caddy_routes()

    def update_caddy_routes(self):
        print("DEBUG: Updating Caddy routes...")
        registry = self.load_registry()
        lines = []
        for name, data in registry.items():
            port = data['port']
            # Generate the Caddy handle block
            lines.append(f"handle /{name}* {{")
            lines.append(f"    reverse_proxy 127.0.0.1:{port}")
            lines.append("}")
        
        try:
            with open(CADDY_ROUTES_FILE, 'w') as f:
                f.write("\n".join(lines))
            # Ensure Caddy (on host) can read it
            os.chmod(CADDY_ROUTES_FILE, 0o644)
        except Exception as e:
            print(f"ERROR writing Caddy routes: {e}")

orchestrator = Orchestrator()

# -----------------------------------------------------------------------------
# ORIGINAL LOGIC
# -----------------------------------------------------------------------------

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, 'r') as file:
        return yaml.safe_load(file) or {}

def save_config(config):
    with open(CONFIG_FILE, 'w') as file:
        yaml.dump(config, file)

def check_instance_status(instance_url, internal_url=None):
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('window-size=1920x1080')
    options.add_argument('--ignore-certificate-errors')

    # If running in Docker, we might need to specify the remote webdriver, 
    # but for this "hybrid" app, we assume local Chrome is installed or handled.
    try:
        driver = webdriver.Chrome(options=options)
    except Exception as e:
        # In dev mode without Chrome installed, this might fail.
        print(f"DEBUG SCRAPER: Failed to initialize Chrome: {e}")
        return "offline", None, None

    status = "offline"
    active_world = None
    background_url = None
    
    # Use internal URL for scraping if provided, otherwise public
    target_url = internal_url if internal_url else instance_url

    try:
        driver.get(target_url)
        
        # [Scraping Logic Preserved]
        if "/join" in driver.current_url:
            try:
                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "current-players")))
            except TimeoutException:
                pass
            world_name = driver.title
            if world_name:
                try:
                    background_url = driver.execute_script("""
                        var background = getComputedStyle(document.body).getPropertyValue('--background-url').trim();
                        background = background.replace(/^url\\(["']?/, '').replace(/["']?\\)$/, '');
                        return background;
                    """)
                except: pass
                
                try:
                    count_elements = driver.find_elements(By.CSS_SELECTOR, ".current-players .count")
                    if len(count_elements) >= 2:
                        player_info = f"{count_elements[0].text} / {count_elements[1].text}"
                    else:
                        match = re.search(r"Current Players\s*(\d+)\s*/\s*(\d+)", driver.find_element(By.TAG_NAME, "body").text, re.DOTALL)
                        player_info = f"{match.group(1)} / {match.group(2)}" if match else "Unknown"
                except: player_info = "Unknown"

                active_world = {'name': world_name, 'background': background_url, 'players': player_info}
                status = "active"
        elif "/game" in driver.current_url:
            status = "online"
        elif "Foundry Virtual Tabletop" in driver.title or "/auth" in driver.current_url or "/setup" in driver.current_url:
            status = "online"
            
    except (TimeoutException, WebDriverException):
        status = "offline"
    finally:
        try:
            driver.quit()
        except: pass

    return status, active_world, background_url

def update_instance_statuses():
    global instance_data_cache
    config = load_config()
    final_instances = []
    public_host = config.get('public_host', DEFAULT_PUBLIC_HOST)

    # Process Static Instances (config.yaml)
    if 'instances' in config:
        for instance in config['instances']:
            status, active_world, background_url = check_instance_status(instance['url'])
            final_instances.append({
                'name': instance['name'],
                'url': instance['url'],
                'type': 'static',
                'status': status,
                'active_world': active_world,
                'background': background_url if background_url else '/static/images/background.jpg'
            })

    # Process Managed Instances (Orchestrator)
    registry = orchestrator.load_registry()
    
    for name, data in registry.items():
        port = data['port']
        
        # [CRITICAL] This is where the Path-Based URL logic goes
        public_url = f"{public_host}/{name}"
        
        # Use localhost for internal scraping
        internal_url = f"http://127.0.0.1:{port}"

        status, active_world, background_url = check_instance_status(public_url, internal_url=internal_url)
        
        final_instances.append({
            'name': name,
            'url': public_url,  # <--- Uses the variable defined above
            'type': 'managed',
            'port': port,
            'template': data.get('template', 'Empty'),
            'status': status,
            'active_world': active_world,
            'background': background_url if background_url else '/static/images/background.jpg'
        })

    instance_data_cache = final_instances

# --- Authentication Decorators ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated_function

# --- Routes ---

@app.route('/api/instance-status')
def api_instance_status():
    return jsonify(instance_data_cache)

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    password = data.get('password')
    role = data.get('role', 'admin')
    config = load_config()
    if role == 'admin':
        if config.get('admin_password_hash') and check_password_hash(config['admin_password_hash'], password):
            session['admin_logged_in'] = True
            return jsonify({'success': True})
    return jsonify({'success': False}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/init', methods=['POST'])
def init_config():
    if os.path.exists(CONFIG_FILE) and load_config().get('admin_password_hash'):
         return jsonify({'error': 'Already configured'}), 403
    data = request.json
    config = {
        'admin_password_hash': generate_password_hash(data.get('admin_password')),
        'shared_data_mode': False,
        'instances': []
    }
    save_config(config)
    return jsonify({'success': True})

@app.route('/api/templates', methods=['GET'])
@admin_required
def get_templates():
    templates = []
    if os.path.exists(TEMPLATES_DIR):
        templates = [d for d in os.listdir(TEMPLATES_DIR) if os.path.isdir(os.path.join(TEMPLATES_DIR, d))]
    
    response = {
        'templates': templates,
        'versions': ['v12', 'v13']
    }
    return jsonify(response)

@app.route('/api/create_instance', methods=['POST'])
@admin_required
def create_instance():
    data = request.json
    name = re.sub(r'[^a-z0-9_-]', '', data.get('name', '').lower())
    source = data.get('source')

    if not name or not source:
        return jsonify({'error': 'Missing name or source'}), 400

    try:
        port = orchestrator.create_instance(name, source)
        update_instance_statuses()
        return jsonify({'success': True, 'port': port})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/config', methods=['GET', 'POST'])
@admin_required
def handle_config():
    if request.method == 'GET':
        config = load_config()
        safe_config = {
            'shared_data_mode': config.get('shared_data_mode', False),
            'instances': config.get('instances', []),
            'public_host': config.get('public_host', DEFAULT_PUBLIC_HOST),
            'viewer_access_enabled': bool(config.get('viewer_password_hash'))
        }
        return jsonify(safe_config)
    
    if request.method == 'POST':
        new_data = request.json
        config = load_config()
        if 'shared_data_mode' in new_data: config['shared_data_mode'] = new_data['shared_data_mode']
        if 'instances' in new_data: config['instances'] = new_data['instances']
        if 'public_host' in new_data: config['public_host'] = new_data['public_host']
        if 'new_admin_password' in new_data and new_data['new_admin_password']:
            config['admin_password_hash'] = generate_password_hash(new_data['new_admin_password'])
        save_config(config)
        update_instance_statuses()
        return jsonify({'success': True})

@app.route('/')
def home():
    config = load_config()
    is_configured = bool(config.get('admin_password_hash'))
    return render_template('index.html', 
                           instances=instance_data_cache, 
                           shared_data_mode=config.get('shared_data_mode', False),
                           is_configured=is_configured,
                           is_admin=session.get('admin_logged_in', False))

scheduler = BackgroundScheduler()
scheduler.add_job(func=update_instance_statuses, trigger="interval", seconds=10)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

if __name__ == '__main__':
    orchestrator.reconcile()
    update_instance_statuses()
    app.run(host='0.0.0.0', port=5000)