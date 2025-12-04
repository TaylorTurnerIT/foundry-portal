import yaml
import os
from werkzeug.security import generate_password_hash
from getpass import getpass

CONFIG_FILE = 'config.yaml'

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, 'r') as file:
        return yaml.safe_load(file) or {}

def save_config(config):
    with open(CONFIG_FILE, 'w') as file:
        yaml.dump(config, file)

def reset_password():
    print("Foundry Portal Admin Password Reset")
    print("-----------------------------------")
    
    config = load_config()
    
    while True:
        password = getpass("Enter new admin password: ")
        confirm = getpass("Confirm new admin password: ")
        
        if password == confirm:
            if password.strip() == "":
                print("Password cannot be empty.")
                continue
            break
        else:
            print("Passwords do not match. Please try again.")
    
    config['admin_password_hash'] = generate_password_hash(password)
    save_config(config)
    print("\nAdmin password successfully updated in config.yaml.")

if __name__ == "__main__":
    reset_password()
