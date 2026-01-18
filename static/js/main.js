document.addEventListener('DOMContentLoaded', () => {
    const sharedDataMode = document.getElementById('main-script').getAttribute('data-shared-data-mode') === 'true';
    const state = window.portalState || {};

    // --- Modal Elements ---
    const createModal = document.getElementById('create-modal');
    const createBtn = document.getElementById('create-btn');
    const createForm = document.getElementById('create-form');
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

    // Create Instance Button
    if (createBtn) {
        createBtn.addEventListener('click', () => {
            loadTemplates();
            createModal.style.display = 'block';
        });
    }

    // Create Form Submission
    if (createForm) {
        createForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = createForm.querySelector('button');
            const originalText = btn.textContent;
            
            // UI Feedback
            btn.textContent = "Launching...";
            btn.disabled = true;

            const name = document.getElementById('new-instance-name').value;
            const source = document.getElementById('new-instance-source').value;

            try {
                const response = await fetch('/api/create_instance', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, source })
                });

                if (response.ok) {
                    const data = await response.json();
                    alert(`Success! Instance running on port ${data.port}`);
                    createModal.style.display = 'none';
                    // Trigger status refresh immediately
                    fetchStatus(); 
                } else {
                    const err = await response.json();
                    alert('Error: ' + (err.error || 'Unknown error'));
                }
            } catch (err) {
                console.error(err);
                alert('Network error while creating instance.');
            } finally {
                btn.textContent = originalText;
                btn.disabled = false;
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
                        window.location.reload();
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

    // [NEW] Fetch Templates for Dropdown
    async function loadTemplates() {
        const select = document.getElementById('new-instance-source');
        select.innerHTML = '<option value="" disabled selected>Loading...</option>';
        
        try {
            const response = await fetch('/api/templates');
            if (response.ok) {
                const data = await response.json();
                select.innerHTML = '<option value="" disabled selected>Select a source...</option>';

                // Group 1: Fresh Installs (Versions)
                if (data.versions && data.versions.length > 0) {
                    const group = document.createElement('optgroup');
                    group.label = "Fresh Install (Empty World)";
                    data.versions.forEach(v => {
                        const opt = document.createElement('option');
                        opt.value = v;
                        opt.textContent = `Foundry VTT ${v.replace('v', 'Version ')}`;
                        group.appendChild(opt);
                    });
                    select.appendChild(group);
                }

                // Group 2: Templates
                if (data.templates && data.templates.length > 0) {
                    const group = document.createElement('optgroup');
                    group.label = "Clone Template";
                    data.templates.forEach(t => {
                        const opt = document.createElement('option');
                        opt.value = t;
                        opt.textContent = t;
                        group.appendChild(opt);
                    });
                    select.appendChild(group);
                }
            } else {
                select.innerHTML = '<option disabled>Error loading templates</option>';
            }
        } catch (err) {
            console.error(err);
            select.innerHTML = '<option disabled>Network Error</option>';
        }
    }

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
        const worldsGallery = document.getElementById('worlds-gallery');
        const instanceList = document.getElementById('instance-list');

        worldsGallery.innerHTML = '';
        instanceList.innerHTML = '';

        let activeWorldsFound = false;

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

            // Update Active Worlds
            if (instance.status === 'active' && instance.active_world) {
                activeWorldsFound = true;
                const worldCard = document.createElement('div');
                worldCard.className = 'world-card';
                const backgroundUrl = instance.active_world.background.startsWith('/')
                    ? new URL(instance.url).origin + instance.active_world.background
                    : instance.url + instance.active_world.background;
                worldCard.style.backgroundImage = `url('${backgroundUrl}')`;
                worldCard.style.backgroundSize = 'cover';
                worldCard.style.backgroundPosition = 'center';

                const worldName = document.createElement('h3');
                worldName.textContent = instance.active_world.name;
                worldCard.appendChild(worldName);

                const playerInfo = document.createElement('p');
                playerInfo.textContent = `Players: ${instance.active_world.players}`;
                worldCard.appendChild(playerInfo);

                const instanceInfo = document.createElement('p');
                instanceInfo.textContent = `Hosted on: ${instance.name}`;
                worldCard.appendChild(instanceInfo);

                worldCard.addEventListener('click', () => {
                    window.open(`${instance.url}/join`, '_blank');
                });

                worldsGallery.appendChild(worldCard);
            }
        });

        if (!activeWorldsFound) {
            worldsGallery.innerHTML = '<p class="no-worlds">No active worlds found.</p>';
        }
    }

    // Initial fetch and interval
    if (state.isConfigured && !state.viewerLocked) {
        fetchStatus();
        setInterval(fetchStatus, 5000);
    }
});
