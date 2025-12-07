import os
import yaml
import re
import json
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
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
app.secret_key = os.urandom(24)  # Secret key for session management

# Global cache to store instance data
instance_data_cache = []
CONFIG_FILE = 'config.yaml'
WORLDS_FILE = 'worlds.json'

def load_config():
    """
    Load the configuration from the 'config.yaml' file.
    Returns:
        dict: Configuration dictionary.
    """
    if not os.path.exists(CONFIG_FILE):
        print(f"DEBUG: Config file {CONFIG_FILE} NOT found.")
        return {}
    with open(CONFIG_FILE, 'r') as file:
        config = yaml.safe_load(file) or {}
        print(f"DEBUG: Loaded config. Keys: {config.keys()}")
        if 'admin_password_hash' in config:
            print("DEBUG: admin_password_hash is present.")
        else:
            print("DEBUG: admin_password_hash is MISSING.")
        return config

def save_config(config):
    """
    Save the configuration to 'config.yaml'.
    """
    with open(CONFIG_FILE, 'w') as file:
        yaml.dump(config, file)

def load_worlds():
    """Load world history from worlds.json file."""
    if not os.path.exists(WORLDS_FILE):
        return {"worlds": {}, "schema_version": 1}
    try:
        with open(WORLDS_FILE, 'r') as file:
            return json.load(file)
    except (json.JSONDecodeError, IOError):
        print(f"ERROR: Could not load {WORLDS_FILE}, returning empty data")
        return {"worlds": {}, "schema_version": 1}

def save_worlds(worlds_data):
    """Save world history to worlds.json file with atomic write."""
    temp_file = WORLDS_FILE + '.tmp'
    try:
        with open(temp_file, 'w') as file:
            json.dump(worlds_data, file, indent=2)
        os.replace(temp_file, WORLDS_FILE)  # Atomic on POSIX
    except IOError as e:
        print(f"ERROR: Could not save {WORLDS_FILE}: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)

def update_world_statuses(instances):
    """Update world statuses based on current instance states."""
    worlds_data = load_worlds()
    worlds = worlds_data.get("worlds", {})
    now = datetime.utcnow().isoformat() + 'Z'

    # First, mark all worlds as offline
    for world in worlds.values():
        world['status'] = 'offline'

    # Then update based on current instance states
    for instance in instances:
        instance_name = instance['name']
        instance_url = instance['url']
        instance_status = instance['status']

        if instance_status == 'active' and instance.get('active_world'):
            # World is running
            active_world = instance['active_world']
            world_key = f"{instance_name}::{active_world['name']}"

            if world_key in worlds:
                # Update existing world
                world = worlds[world_key]
                world['last_seen'] = now
                world['status'] = 'active'
                world['times_seen'] = world.get('times_seen', 0) + 1
                if active_world.get('background'):
                    world['cached_background_url'] = active_world['background']
            else:
                # Create new world entry
                worlds[world_key] = {
                    'name': active_world['name'],
                    'instance_name': instance_name,
                    'instance_url': instance_url,
                    'first_seen': now,
                    'last_seen': now,
                    'status': 'active',
                    'cached_background_url': active_world.get('background'),
                    'times_seen': 1
                }
        else:
            # Instance is online or offline, mark any of its worlds as idle/offline
            for world_key, world in worlds.items():
                if world['instance_name'] == instance_name:
                    if instance_status == 'online':
                        world['status'] = 'idle'
                    else:
                        world['status'] = 'offline'

    worlds_data['worlds'] = worlds
    save_worlds(worlds_data)

def get_all_worlds_sorted():
    """Get all worlds sorted by status (active, idle, offline) then by last_seen desc."""
    worlds_data = load_worlds()
    worlds_list = list(worlds_data.get("worlds", {}).values())

    # Define sort priority: active=0, idle=1, offline=2
    def get_sort_key(world):
        status_priority = {'active': 0, 'idle': 1, 'offline': 2}
        return (
            status_priority.get(world.get('status', 'offline'), 2),
            -datetime.fromisoformat(world.get('last_seen', '1970-01-01T00:00:00Z').replace('Z', '+00:00')).timestamp()
        )

    worlds_list.sort(key=get_sort_key)
    return worlds_list

def check_instance_status(instance_url):
    """
    Check the status of a Foundry instance by navigating to its URL using Selenium.
    Uses regex to parse player counts from the body text.
    """
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('window-size=1920x1080')
    options.add_argument('--ignore-certificate-errors')

    driver = webdriver.Chrome(options=options)

    status = "offline"
    active_world = None
    background_url = None

    try:
        driver.get(instance_url)

        # DEBUG: Print what the scraper actually sees
        print(f"DEBUG SCRAPER: URL={driver.current_url}, Title={driver.title}")
        print(f"DEBUG SCRAPER: Checking conditions...")

        # Check for /join first (active world)
        if "/join" in driver.current_url:
            print(f"DEBUG SCRAPER: Matched /join condition")
            # Wait for the page to load - check for the current-players element
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "current-players"))
                )
            except TimeoutException:
                print("DEBUG SCRAPER: Timeout waiting for current-players element")

            world_name = driver.title

            if world_name:
                # Try to get background
                try:
                    background_url = driver.execute_script("""
                        var background = getComputedStyle(document.body).getPropertyValue('--background-url').trim();
                        background = background.replace(/^url\\(["']?/, '').replace(/["']?\\)$/, '');
                        return background;
                    """)
                except:
                    pass

                # Get player count by querying the specific HTML elements
                try:
                    count_elements = driver.find_elements(By.CSS_SELECTOR, ".current-players .count")
                    if len(count_elements) >= 2:
                        current = count_elements[0].text
                        max_players = count_elements[1].text
                        player_info = f"{current} / {max_players}"
                        print(f"DEBUG SCRAPER: Found player info from elements: {player_info}")
                    else:
                        # Fallback to regex on body text
                        body_text = driver.find_element(By.TAG_NAME, "body").text
                        # Account for newlines and whitespace between elements
                        match = re.search(r"Current Players\s*(\d+)\s*/\s*(\d+)", body_text, re.DOTALL)
                        if match:
                            player_info = f"{match.group(1)} / {match.group(2)}"
                            print(f"DEBUG SCRAPER: Found player info from text: {player_info}")
                        else:
                            player_info = "Unknown / Unknown"
                            print(f"DEBUG SCRAPER: No player match found")
                except Exception as e:
                    player_info = "Unknown / Unknown"
                    print(f"DEBUG SCRAPER: Exception getting player count: {e}")

                if world_name:
                    active_world = {
                        'name': world_name,
                        'background': background_url,
                        'players': player_info
                    }
                    status = "active"
        # Check for /game (player is in a game)
        elif "/game" in driver.current_url:
            print(f"DEBUG SCRAPER: Matched /game condition")
            status = "online"
            # Try to get background
            try:
                background_url = driver.execute_script("""
                    var background = getComputedStyle(document.body).getPropertyValue('--background-url').trim();
                    background = background.replace(/^url\\(["']?/, '').replace(/["']?\\)$/, '');
                    return background;
                """)
            except:
                pass
        elif "Foundry Virtual Tabletop" in driver.title or "/auth" in driver.current_url or "/setup" in driver.current_url:
            print(f"DEBUG SCRAPER: Matched online condition (title/auth/setup)")
            status = "online"
            
            # Try to get background (same as before)
            try:
                background_url = driver.execute_script("""
                    var background = getComputedStyle(document.body).getPropertyValue('--background-url').trim();
                    background = background.replace(/^url\\(["']?/, '').replace(/["']?\\)$/, '');
                    return background;
                """)
            except:
                pass
    except (TimeoutException, WebDriverException) as e:
        print(f"DEBUG SCRAPER ERROR: {e}")
        status = "offline"
    finally:
        driver.quit()

    print(f"DEBUG SCRAPER: Final status={status}, active_world={active_world}")
    return status, active_world, background_url

def initialize_instance_data():
    global instance_data_cache
    config = load_config()
    instances = []

    if 'instances' in config:
        for instance in config['instances']:
            instance_data = {
                'name': instance['name'],
                'url': instance['url'],
                'status': 'offline',
                'active_world': None,
                'background': '/static/images/background.jpg'
            }
            instances.append(instance_data)

    instance_data_cache = instances

def update_instance_statuses():
    global instance_data_cache
    config = load_config()
    instances = []

    if 'instances' in config:
        for instance in config['instances']:
            status, active_world, background_url = check_instance_status(instance['url'])
            instance_data = {
                'name': instance['name'],
                'url': instance['url'],
                'status': status,
                'active_world': active_world,
                'background': background_url if background_url else '/static/images/background.jpg'
            }
            instances.append(instance_data)

    instance_data_cache = instances

    # Update world history based on current instance states
    update_world_statuses(instances)

    print("Instance statuses updated.")

# --- Authentication Decorators ---

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated_function

def viewer_auth_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        config = load_config()
        # If viewer password is set and user is not logged in as viewer or admin
        if config.get('viewer_password_hash') and not (session.get('viewer_logged_in') or session.get('admin_logged_in')):
             # For API calls, return 401. For page loads, we might handle differently in frontend, 
             # but here we just check session.
             # Actually, for the main page, we pass a flag to the template.
             pass 
        return f(*args, **kwargs)
    return decorated_function

# --- Routes ---

@app.route('/api/instance-status')
def api_instance_status():
    return jsonify(instance_data_cache)

@app.route('/api/worlds')
def api_worlds():
    """Return all worlds (active and historical) sorted by relevance."""
    worlds = get_all_worlds_sorted()
    return jsonify(worlds)

@app.route('/api/worlds/<path:world_key>', methods=['DELETE'])
@admin_required
def delete_world(world_key):
    """Delete a single world from history."""
    worlds_data = load_worlds()
    worlds = worlds_data.get("worlds", {})

    if world_key in worlds:
        del worlds[world_key]
        worlds_data['worlds'] = worlds
        save_worlds(worlds_data)
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'World not found'}), 404

@app.route('/api/worlds', methods=['DELETE'])
@admin_required
def clear_worlds():
    """Clear all world history."""
    worlds_data = {"worlds": {}, "schema_version": 1}
    save_worlds(worlds_data)
    return jsonify({'success': True})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    password = data.get('password')
    role = data.get('role', 'admin') # 'admin' or 'viewer'
    config = load_config()

    if role == 'admin':
        if config.get('admin_password_hash') and check_password_hash(config['admin_password_hash'], password):
            session['admin_logged_in'] = True
            return jsonify({'success': True})
    elif role == 'viewer':
        if config.get('viewer_password_hash') and check_password_hash(config['viewer_password_hash'], password):
            session['viewer_logged_in'] = True
            return jsonify({'success': True})
    
    return jsonify({'success': False, 'error': 'Invalid password'}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/config', methods=['GET', 'POST'])
@admin_required
def handle_config():
    if request.method == 'GET':
        config = load_config()
        # Don't send hashes back
        safe_config = {
            'shared_data_mode': config.get('shared_data_mode', False),
            'instances': config.get('instances', []),
            'viewer_access_enabled': bool(config.get('viewer_password_hash'))
        }
        return jsonify(safe_config)
    
    if request.method == 'POST':
        new_data = request.json
        config = load_config()
        
        # Update fields
        if 'shared_data_mode' in new_data:
            config['shared_data_mode'] = new_data['shared_data_mode']
        if 'instances' in new_data:
            config['instances'] = new_data['instances']
        
        # Handle password updates
        if 'new_admin_password' in new_data and new_data['new_admin_password']:
            config['admin_password_hash'] = generate_password_hash(new_data['new_admin_password'])
            
        if 'new_viewer_password' in new_data:
            if new_data['new_viewer_password']:
                config['viewer_password_hash'] = generate_password_hash(new_data['new_viewer_password'])
            else:
                # If empty, disable viewer access (remove hash)
                config.pop('viewer_password_hash', None)

        save_config(config)
        # Trigger update immediately
        update_instance_statuses()
        return jsonify({'success': True})

@app.route('/api/init', methods=['POST'])
def init_config():
    """Endpoint for initial setup if no config exists."""
    if os.path.exists(CONFIG_FILE) and load_config().get('admin_password_hash'):
         return jsonify({'error': 'Already configured'}), 403
    
    data = request.json
    password = data.get('admin_password')
    if not password:
        return jsonify({'error': 'Password required'}), 400
        
    config = {
        'admin_password_hash': generate_password_hash(password),
        'shared_data_mode': False,
        'instances': []
    }
    save_config(config)
    return jsonify({'success': True})

@app.route('/')
def home():
    config = load_config()
    
    # Check if configured
    is_configured = bool(config.get('admin_password_hash'))
    print(f"DEBUG: home route - is_configured: {is_configured}")
    
    # Check viewer access
    viewer_locked = False
    if is_configured and config.get('viewer_password_hash'):
        if not (session.get('viewer_logged_in') or session.get('admin_logged_in')):
            viewer_locked = True
    
    print(f"DEBUG: home route - viewer_locked: {viewer_locked}")

    return render_template('index.html', 
                           instances=instance_data_cache, 
                           shared_data_mode=config.get('shared_data_mode', False),
                           is_configured=is_configured,
                           viewer_locked=viewer_locked,
                           is_admin=session.get('admin_logged_in', False))

# Initialize the background scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(func=update_instance_statuses, trigger="interval", seconds=10)
scheduler.start()

atexit.register(lambda: scheduler.shutdown())

if __name__ == '__main__':
    initialize_instance_data()
    app.run(host='0.0.0.0', port=5000)
