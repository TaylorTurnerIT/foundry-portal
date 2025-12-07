document.addEventListener('DOMContentLoaded', () => {
    const sharedDataMode = document.getElementById('main-script').getAttribute('data-shared-data-mode') === 'true';
    const state = window.portalState || {};

    // --- Modal Elements ---
    const initModal = document.getElementById('init-modal');
    const loginModal = document.getElementById('login-modal');
    const configModal = document.getElementById('config-modal');
    const viewerLock = document.getElementById('viewer-lock');
    const adminBtn = document.getElementById('admin-btn');
    const closeBtns = document.querySelectorAll('.close');

    // --- Initialization Flow ---
    if (!state.isConfigured) {
        initModal.style.display = 'block';
    } else if (state.viewerLocked) {
        viewerLock.style.display = 'block';
    }

    // --- Event Listeners ---

    // Admin Button
    if (adminBtn) {
        adminBtn.addEventListener('click', () => {
            if (state.isAdmin) {
                openConfigModal();
            } else {
                openLoginModal();
            }
        });
    }

    // Close Modals
    closeBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            btn.closest('.modal').style.display = 'none';
        });
    });

    // Init Form
    const initForm = document.getElementById('init-form');
    if (initForm) {
        initForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const password = document.getElementById('init-password').value;
            try {
                const response = await fetch('/api/init', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ admin_password: password })
                });
                if (response.ok) {
                    location.reload();
                } else {
                    alert('Initialization failed');
                }
            } catch (err) {
                console.error(err);
                alert('Error initializing');
            }
        });
    }

    // Login Form
    const loginForm = document.getElementById('login-form');
    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const password = document.getElementById('login-password').value;
            const role = document.getElementById('login-role').value;

            try {
                const response = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password, role })
                });

                if (response.ok) {
                    loginModal.style.display = 'none';
                    if (role === 'admin') {
                        state.isAdmin = true;
                        openConfigModal();
                    }
                } else {
                    alert('Invalid password');
                }
            } catch (err) {
                console.error(err);
                alert('Login error');
            }
        });
    }

    // Viewer Form
    const viewerForm = document.getElementById('viewer-form');
    if (viewerForm) {
        viewerForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const password = document.getElementById('viewer-password').value;

            try {
                const response = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password, role: 'viewer' })
                });

                if (response.ok) {
                    location.reload();
                } else {
                    alert('Invalid password');
                }
            } catch (err) {
                console.error(err);
                alert('Login error');
            }
        });
    }

    // Config Form
    const configForm = document.getElementById('config-form');
    if (configForm) {
        configForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = {
                shared_data_mode: document.getElementById('shared-data-mode').checked,
                instances: getInstancesFromDOM(),
                new_admin_password: document.getElementById('new-admin-password').value,
                new_viewer_password: document.getElementById('new-viewer-password').value
            };

            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(formData)
                });

                if (response.ok) {
                    alert('Configuration saved!');
                    configModal.style.display = 'none';
                    location.reload();
                } else {
                    alert('Failed to save configuration');
                }
            } catch (err) {
                console.error(err);
                alert('Error saving configuration');
            }
        });
    }

    // Add Instance Button
    const addInstanceBtn = document.getElementById('add-instance-btn');
    if (addInstanceBtn) {
        addInstanceBtn.addEventListener('click', () => {
            addInstanceRow();
        });
    }

    // --- Helper Functions ---

    function openLoginModal() {
        loginModal.style.display = 'block';
        document.getElementById('login-password').value = '';
        document.getElementById('login-password').focus();
    }

    async function openConfigModal() {
        try {
            const response = await fetch('/api/config');
            if (response.ok) {
                const config = await response.json();
                populateConfigForm(config);
                configModal.style.display = 'block';
            } else {
                // Session might have expired
                state.isAdmin = false;
                openLoginModal();
            }
        } catch (err) {
            console.error(err);
        }
    }

    function populateConfigForm(config) {
        document.getElementById('shared-data-mode').checked = config.shared_data_mode;
        const container = document.getElementById('instances-container');
        container.innerHTML = '';
        config.instances.forEach(inst => addInstanceRow(inst));

        // Reset password fields
        document.getElementById('new-admin-password').value = '';
        document.getElementById('new-viewer-password').value = '';
    }

    function addInstanceRow(data = { name: '', url: '' }) {
        const container = document.getElementById('instances-container');
        const div = document.createElement('div');
        div.className = 'instance-row form-group';
        div.innerHTML = `
            <input type="text" placeholder="Name" value="${data.name}" class="instance-name" required>
            <input type="url" placeholder="URL" value="${data.url}" class="instance-url" required>
            <button type="button" class="btn-danger remove-instance">&times;</button>
        `;

        div.querySelector('.remove-instance').addEventListener('click', () => {
            div.remove();
        });

        container.appendChild(div);
    }

    function getInstancesFromDOM() {
        const rows = document.querySelectorAll('.instance-row');
        return Array.from(rows).map(row => ({
            name: row.querySelector('.instance-name').value,
            url: row.querySelector('.instance-url').value
        }));
    }

    // --- Polling for Status (Existing Logic) ---
    function fetchStatus() {
        if (state.viewerLocked || !state.isConfigured) return;

        fetch('/api/instance-status')
            .then(response => response.json())
            .then(data => {
                updateDashboard(data);
            })
            .catch(error => console.error('Error fetching status:', error));
    }

    function updateDashboard(instances) {
        const instanceList = document.getElementById('instance-list');
        instanceList.innerHTML = '';

        // Store instances for world card click handling
        window.instanceCache = instances;

        instances.forEach(instance => {
            // Update Instance List
            const instanceCard = document.createElement('div');
            instanceCard.className = `instance-card ${instance.status}`;

            // Add background image if available
            if (instance.background) {
                const backgroundUrl = instance.background.startsWith('/')
                    ? new URL(instance.url).origin + instance.background
                    : instance.url + instance.background;
                instanceCard.style.backgroundImage = `url('${backgroundUrl}')`;
                instanceCard.style.backgroundSize = 'cover';
                instanceCard.style.backgroundPosition = 'center';
            }

            instanceCard.innerHTML = `
                <div class="instance-info-container">
                    <div class="instance-header">
                        <span class="status-indicator ${instance.status}"></span>
                        <h3>${instance.name}</h3>
                    </div>
                    <p class="instance-url"><a href="${instance.url}" target="_blank">${instance.url}</a></p>
                </div>
            `;
            instanceList.appendChild(instanceCard);
        });
    }

    // --- Worlds Management ---

    function fetchWorlds() {
        if (state.viewerLocked || !state.isConfigured) return;

        fetch('/api/worlds')
            .then(response => response.json())
            .then(worlds => {
                window.allWorlds = worlds;  // Store for search filtering
                updateWorldsGallery(worlds);
            })
            .catch(error => console.error('Error fetching worlds:', error));
    }

    function formatRelativeTime(isoString, isActive) {
        if (isActive) return 'now';

        const date = new Date(isoString);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);
        const diffWeeks = Math.floor(diffDays / 7);
        const diffMonths = Math.floor(diffDays / 30);

        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins} minute${diffMins !== 1 ? 's' : ''} ago`;
        if (diffHours < 24) return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`;
        if (diffDays < 7) return `${diffDays} day${diffDays !== 1 ? 's' : ''} ago`;
        if (diffDays < 30) return `${diffWeeks} week${diffWeeks !== 1 ? 's' : ''} ago`;
        return `${diffMonths} month${diffMonths !== 1 ? 's' : ''} ago`;
    }

    function loadWorldBackground(card, cachedUrl, instanceUrl) {
        if (!cachedUrl) {
            card.style.backgroundImage = 'url(/static/images/background.jpg)';
            return;
        }

        // Resolve URL
        let fullUrl;
        if (cachedUrl.startsWith('http')) {
            fullUrl = cachedUrl;
        } else if (cachedUrl.startsWith('/')) {
            fullUrl = new URL(instanceUrl).origin + cachedUrl;
        } else {
            fullUrl = instanceUrl + '/' + cachedUrl;
        }

        // Try to load the image
        const img = new Image();
        img.onload = () => {
            card.style.backgroundImage = `url('${fullUrl}')`;
        };
        img.onerror = () => {
            // Fallback to placeholder
            card.style.backgroundImage = 'url(/static/images/background.jpg)';
        };
        img.src = fullUrl;
    }

    function getStatusTooltip(status) {
        const tooltips = {
            'active': 'World is currently running',
            'idle': 'Instance online, world not running',
            'offline': 'Instance is offline'
        };
        return tooltips[status] || 'Unknown status';
    }

    function updateWorldsGallery(worlds) {
        const worldsGallery = document.getElementById('worlds-gallery');
        worldsGallery.innerHTML = '';

        if (worlds.length === 0) {
            worldsGallery.innerHTML = '<p class="no-worlds">No worlds discovered yet.</p>';
            return;
        }

        worlds.forEach(world => {
            const worldCard = document.createElement('div');
            worldCard.className = `world-card ${world.status}`;
            worldCard.setAttribute('data-world-name', world.name.toLowerCase());

            // Load background with fallback chain
            loadWorldBackground(worldCard, world.cached_background_url, world.instance_url);

            // Status indicator
            const statusDot = document.createElement('span');
            statusDot.className = `world-status ${world.status}`;
            statusDot.title = getStatusTooltip(world.status);
            worldCard.appendChild(statusDot);

            // Play icon for active worlds
            if (world.status === 'active') {
                const playIcon = document.createElement('i');
                playIcon.className = 'fas fa-play-circle play-icon';
                worldCard.appendChild(playIcon);

                worldCard.addEventListener('click', () => {
                    window.open(`${world.instance_url}/join`, '_blank');
                });
            }

            // World info container
            const infoContainer = document.createElement('div');
            infoContainer.className = 'world-info';

            const worldName = document.createElement('h3');
            worldName.textContent = world.name;
            infoContainer.appendChild(worldName);

            const instanceInfo = document.createElement('p');
            instanceInfo.className = 'world-instance';
            instanceInfo.textContent = `on ${world.instance_name}`;
            infoContainer.appendChild(instanceInfo);

            const timeInfo = document.createElement('p');
            timeInfo.className = 'world-time';
            timeInfo.textContent = formatRelativeTime(world.last_seen, world.status === 'active');
            infoContainer.appendChild(timeInfo);

            // Show players only for active worlds
            if (world.status === 'active') {
                const instance = window.instanceCache?.find(i => i.name === world.instance_name);
                if (instance && instance.active_world && instance.active_world.name === world.name) {
                    const playerInfo = document.createElement('p');
                    playerInfo.className = 'world-players';
                    playerInfo.textContent = `Players: ${instance.active_world.players}`;
                    infoContainer.appendChild(playerInfo);
                }
            }

            worldCard.appendChild(infoContainer);

            // Admin delete button
            if (state.isAdmin) {
                const deleteBtn = document.createElement('button');
                deleteBtn.className = 'delete-world';
                deleteBtn.innerHTML = '&times;';
                deleteBtn.title = 'Remove from history';
                deleteBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    deleteWorldFromHistory(world);
                });
                worldCard.appendChild(deleteBtn);
            }

            worldsGallery.appendChild(worldCard);
        });
    }

    function deleteWorldFromHistory(world) {
        if (!confirm(`Remove "${world.name}" from world history?`)) {
            return;
        }

        const worldKey = `${world.instance_name}::${world.name}`;
        fetch(`/api/worlds/${encodeURIComponent(worldKey)}`, {
            method: 'DELETE'
        })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    fetchWorlds();  // Refresh the list
                } else {
                    alert('Failed to delete world');
                }
            })
            .catch(error => {
                console.error('Error deleting world:', error);
                alert('Error deleting world');
            });
    }

    // Real-time search filter
    const searchInput = document.getElementById('world-search');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase();
            const worldCards = document.querySelectorAll('.world-card');

            worldCards.forEach(card => {
                const worldName = card.getAttribute('data-world-name');
                if (worldName && worldName.includes(query)) {
                    card.style.display = '';
                } else {
                    card.style.display = 'none';
                }
            });
        });
    }

    // Initial fetch and interval
    if (state.isConfigured && !state.viewerLocked) {
        fetchStatus();
        fetchWorlds();
        setInterval(fetchStatus, 5000);
        setInterval(fetchWorlds, 5000);
    }
});
