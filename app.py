import os
import yaml
import re
import json
import shutil
import docker
import time
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

# -----------------------------------------------------------------------------
# DYNAMIC DATA DIRECTORY CONFIGURATION
# -----------------------------------------------------------------------------
# Priority:
# 1. Environment Variable 'FOUNDRY_DATA_DIR'
# 2. Production Path '/data/foundry'
# 3. Local Development Path './foundry-data'

if os.environ.get('FOUNDRY_DATA_DIR'):
    DATA_DIR = os.environ.get('FOUNDRY_DATA_DIR')
elif os.path.exists('/data/foundry'):
    DATA_DIR = '/data/foundry'
else:
    # Local development fallback
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'foundry-data')
    if not os.path.exists(DATA_DIR):
        print(f"DEBUG: DEV MODE - Creating local data directory at: {DATA_DIR}")
        os.makedirs(DATA_DIR, exist_ok=True)
    else:
        print(f"DEBUG: DEV MODE - Using existing local data directory at: {DATA_DIR}")

# Define subdirectories based on the determined DATA_DIR
REGISTRY_FILE = os.path.join(DATA_DIR, 'instances.json')
TEMPLATES_DIR = os.path.join(DATA_DIR, 'templates')
WORLDS_DIR = os.path.join(DATA_DIR, 'worlds')
DOCKER_NETWORK = 'foundry_net'
BASE_PORT = 30000 

# Ensure subdirectories exist
os.makedirs(WORLDS_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# -----------------------------------------------------------------------------
# ORCHESTRATOR CLASS
# -----------------------------------------------------------------------------
class Orchestrator:
    def __init__(self):
        try:
            # Detect if running in Docker or Local
            # If local, we might need to talk to a remote docker or local socket
            self.client = docker.from_env()
            print("DEBUG ORCHESTRATOR: Docker client connected.")
        except Exception as e:
            print(f"DEBUG ORCHESTRATOR: Failed to connect to Docker socket: {e}")
            # If in dev mode without Docker, we should handle gracefully or just warn
            print("WARNING: Docker orchestration features will not work without a Docker socket.")
            self.client = None

    def load_registry(self):
        """Load managed instances from JSON."""
        if not os.path.exists(REGISTRY_FILE):
            return {}
        try:
            with open(REGISTRY_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}

    def save_registry(self, registry):
        with open(REGISTRY_FILE, 'w') as f:
            json.dump(registry, f, indent=2)

    def get_next_port(self):
        """Find the next available port starting from 30002."""
        registry = self.load_registry()
        used_ports = {inst['port'] for inst in registry.values()}
        port = 30002
        while port in used_ports:
            port += 1
        return port

    def launch_instance(self, name, port):
        """Spawns the Game and Nursery containers for a specific instance."""
        if not self.client:
            print(f"DEBUG: Skipping launch of {name} (No Docker Client)")
            return False

        game_container_name = f"foundry_{name}"
        nursery_container_name = f"nursery_{name}"
        world_path = os.path.join(WORLDS_DIR, name)

        # 1. Launch Game Container (felddy/foundryvtt)
        try:
            try:
                c = self.client.containers.get(game_container_name)
                if c.status != 'running':
                    c.start()
            except docker.errors.NotFound:
                print(f"DEBUG ORCHESTRATOR: Spawning {game_container_name}...")
                self.client.containers.run(
                    "felddy/foundryvtt:release",
                    name=game_container_name,
                    detach=True,
                    network=DOCKER_NETWORK,
                    # IMPORTANT: In Dev mode, we are mounting a local path to the container.
                    # This works if your Dev Docker is on the same machine.
                    volumes={os.path.abspath(world_path): {'bind': '/data', 'mode': 'rw'}},
                    environment={"CONTAINER_CACHE": "/data/container_cache"}
                )
        except Exception as e:
            print(f"ERROR launching game {name}: {e}")
            return False

        # 2. Launch Nursery Container
        try:
            try:
                c = self.client.containers.get(nursery_container_name)
                if c.status != 'running':
                    c.start()
            except docker.errors.NotFound:
                print(f"DEBUG ORCHESTRATOR: Spawning {nursery_container_name} on port {port}...")
                self.client.containers.run(
                    "ghcr.io/itsthejoker/containernursery:latest",
                    name=nursery_container_name,
                    detach=True,
                    network=DOCKER_NETWORK,
                    ports={'80/tcp': port},
                    environment={
                        "UPSTREAM_HOST": game_container_name,
                        "UPSTREAM_PORT": "30000"
                    }
                )
        except Exception as e:
            print(f"ERROR launching nursery {name}: {e}")
            return False
            
        return True

    def create_instance(self, name, template_name):
        """Creates files from template and registers the instance."""
        if name in self.load_registry():
            raise ValueError("Instance name already exists.")
        
        src = os.path.join(TEMPLATES_DIR, template_name)
        dst = os.path.join(WORLDS_DIR, name)

        if not os.path.exists(src):
            raise ValueError("Template not found.")

        # Copy Files
        print(f"DEBUG ORCHESTRATOR: Copying template {src} to {dst}")
        shutil.copytree(src, dst)

        # Fix Permissions
        # On local dev (Windows/Mac), chown might fail or be unnecessary.
        # We wrap it in a try/except to be safe for dev mode.
        try:
            for root, dirs, files in os.walk(dst):
                os.chown(root, 1000, 1000)
                for d in dirs:
                    os.chown(os.path.join(root, d), 1000, 1000)
                for f in files:
                    os.chown(os.path.join(root, f), 1000, 1000)
        except AttributeError:
            # os.chown not available on Windows
            pass
        except PermissionError:
            print("WARNING: Could not change ownership of world files. Container might have issues if UIDs don't match.")

        # Update Registry
        registry = self.load_registry()
        port = self.get_next_port()
        
        registry[name] = {
            "name": name,
            "port": port,
            "template": template_name,
            "created_at": time.time()
        }
        self.save_registry(registry)

        # Launch
        self.launch_instance(name, port)
        return port

    def reconcile(self):
        """Ensure all registered instances are running."""
        print("DEBUG ORCHESTRATOR: Reconciling instances...")
        registry = self.load_registry()
        for name, data in registry.items():
            self.launch_instance(name, data['port'])

orchestrator = Orchestrator()

# -----------------------------------------------------------------------------
# EXISTING LOGIC (Unchanged)
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

    # 1. Process Static Instances
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

    # 2. Process Managed Instances
    registry = orchestrator.load_registry()
    public_host = config.get('public_host', 'http://localhost') 
    
    for name, data in registry.items():
        port = data['port']
        public_url = f"{public_host}:{port}"
        
        # Optimization: In real production, use internal_url. 
        # In local dev, internal_url (http://nursery_name) won't resolve unless you have custom DNS/Hosts.
        # So for DEV, we just use public_url (localhost:port).
        if os.path.exists('/data/foundry'):
             internal_url = f"http://nursery_{name}:80"
        else:
             internal_url = public_url

        status, active_world, background_url = check_instance_status(public_url, internal_url=internal_url)
        
        final_instances.append({
            'name': name,
            'url': public_url,
            'type': 'managed',
            'port': port,
            'template': data['template'],
            'status': status,
            'active_world': active_world,
            'background': background_url if background_url else '/static/images/background.jpg'
        })

    instance_data_cache = final_instances
    print(f"Instance statuses updated. Total: {len(final_instances)}")

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
    if not os.path.exists(TEMPLATES_DIR):
        return jsonify([])
    templates = [d for d in os.listdir(TEMPLATES_DIR) if os.path.isdir(os.path.join(TEMPLATES_DIR, d))]
    return jsonify(templates)

@app.route('/api/create_instance', methods=['POST'])
@admin_required
def create_instance():
    data = request.json
    name = data.get('name')
    template = data.get('template')
    
    if not name or not template:
        return jsonify({'error': 'Missing name or template'}), 400
        
    name = re.sub(r'[^a-z0-9_-]', '', name.lower())
    
    try:
        port = orchestrator.create_instance(name, template)
        update_instance_statuses()
        return jsonify({'success': True, 'port': port})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        print(f"CREATE ERROR: {e}")
        return jsonify({'error': 'Internal Server Error'}), 500

@app.route('/api/config', methods=['GET', 'POST'])
@admin_required
def handle_config():
    if request.method == 'GET':
        config = load_config()
        safe_config = {
            'shared_data_mode': config.get('shared_data_mode', False),
            'instances': config.get('instances', []),
            'public_host': config.get('public_host', 'http://localhost'),
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