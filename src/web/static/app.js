// ============ API ============

async function apiGet(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

async function apiPost(url, data) {
    const res = await fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data || {}),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({error: tr('web.common.unknown_error')}));
        throw new Error(err.error || `HTTP ${res.status}`);
    }
    return res.json();
}

async function apiPut(url, data) {
    const res = await fetch(url, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data || {}),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({error: tr('web.common.unknown_error')}));
        throw new Error(err.error || `HTTP ${res.status}`);
    }
    return res.json();
}

async function apiDelete(url) {
    const res = await fetch(url, {method: 'DELETE'});
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

// API responses are cached in sessionStorage so a page refresh can paint the
// last known data immediately instead of flashing empty placeholders. The
// fresh response always replaces the cached one in the background.
const API_CACHE_PREFIX = 'telesender.api.v1:';
const API_CACHE_TTL = 10 * 60 * 1000;
const apiCacheApplied = new Set();

function readApiCache(url, once) {
    if (once && apiCacheApplied.has(url)) return null;
    if (once) apiCacheApplied.add(url);

    try {
        const raw = sessionStorage.getItem(API_CACHE_PREFIX + url);
        if (!raw) return null;
        const entry = JSON.parse(raw);
        if (!entry || typeof entry.at !== 'number' || Date.now() - entry.at > API_CACHE_TTL) {
            sessionStorage.removeItem(API_CACHE_PREFIX + url);
            return null;
        }
        return entry.data;
    } catch (e) {
        return null;
    }
}

function writeApiCache(url, data) {
    try {
        sessionStorage.setItem(API_CACHE_PREFIX + url, JSON.stringify({at: Date.now(), data}));
    } catch (e) {}
}

function clearApiCache(url) {
    try {
        sessionStorage.removeItem(API_CACHE_PREFIX + url);
    } catch (e) {}
}

async function apiGetCached(url, apply) {
    const cached = readApiCache(url, true);
    if (cached !== null) {
        try {
            apply(cached);
        } catch (e) {
            console.error('Cached render error:', e);
        }
    }

    const data = await apiGet(url);
    writeApiCache(url, data);
    return data;
}

function scheduleLoading(container, delay = 250) {
    if (!container || container.children.length > 0) return null;

    return setTimeout(() => {
        if (container.children.length > 0) return;
        container.innerHTML = `<div class="empty"><div class="empty-text">${tr('web.common.loading')}</div></div>`;
    }, delay);
}

// ============ Helpers ============

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function tr(key, vars, plain = false) {
    let value = window.WEB_I18N || {};
    const parts = key.split('.');
    if (parts[0] === 'web') {
        if (value.web && typeof value.web === 'object') {
            value = value.web;
        }
        parts.shift();
    }
    for (const part of parts) {
        if (value && typeof value === 'object' && part in value) {
            value = value[part];
        } else {
            return key;
        }
    }
    if (typeof value !== 'string') return key;

    if (vars) {
        for (const [name, replacement] of Object.entries(vars)) {
            const safe = plain ? String(replacement) : escapeHtml(String(replacement));
            value = value.split(`{${name}}`).join(safe);
        }
    }
    return value;
}

// ============ App Dialog (alert / confirm) ============

let appDialogResolve = null;
let appDialogLastFocus = null;

function setupAppDialog() {
    const dialog = document.getElementById('app-dialog');
    if (!dialog || dialog.dataset.ready === '1') return;
    dialog.dataset.ready = '1';

    document.getElementById('app-dialog-cancel').addEventListener('click', () => appDialogClose(false));
    document.getElementById('app-dialog-confirm').addEventListener('click', () => appDialogClose(true));
    document.getElementById('app-dialog-close').addEventListener('click', () => appDialogClose(false));

    document.addEventListener('keydown', (event) => {
        if (!dialog.classList.contains('open')) return;

        if (event.key === 'Escape') {
            event.preventDefault();
            appDialogClose(false);
            return;
        }

        if (event.key !== 'Tab') return;

        const focusable = Array.from(dialog.querySelectorAll('button:not([disabled])'))
            .filter((el) => el.offsetParent !== null);
        if (focusable.length === 0) return;

        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });
}

function appDialogClose(result) {
    const dialog = document.getElementById('app-dialog');
    if (!dialog || !dialog.classList.contains('open')) return;

    dialog.classList.remove('open');
    const resolve = appDialogResolve;
    appDialogResolve = null;

    if (appDialogLastFocus && typeof appDialogLastFocus.focus === 'function') {
        appDialogLastFocus.focus();
    }
    appDialogLastFocus = null;

    if (resolve) resolve(result);
}

function showAppDialog(options) {
    const opts = options || {};
    const dialog = document.getElementById('app-dialog');
    const isConfirm = !!opts.confirm;

    if (!dialog) {
        return Promise.resolve(isConfirm ? false : true);
    }

    if (appDialogResolve) appDialogClose(false);

    appDialogLastFocus = document.activeElement;

    document.getElementById('app-dialog-title').textContent = opts.title
        || tr(isConfirm ? 'web.common.confirm_title' : 'web.common.notice', null, true);
    document.getElementById('app-dialog-message').textContent = opts.message || '';

    const cancelBtn = document.getElementById('app-dialog-cancel');
    const confirmBtn = document.getElementById('app-dialog-confirm');

    cancelBtn.style.display = isConfirm ? '' : 'none';
    cancelBtn.textContent = opts.cancelText || tr('web.common.cancel', null, true);
    confirmBtn.textContent = opts.confirmText
        || tr(isConfirm ? 'web.common.confirm' : 'web.common.close', null, true);
    confirmBtn.classList.toggle('btn-danger', !!opts.danger);
    confirmBtn.classList.toggle('btn-primary', !opts.danger);

    dialog.classList.add('open');

    const focusTarget = isConfirm ? cancelBtn : confirmBtn;
    requestAnimationFrame(() => focusTarget.focus());

    return new Promise((resolve) => {
        appDialogResolve = resolve;
    });
}

function showAlert(message, options) {
    return showAppDialog(Object.assign({message, confirm: false}, options || {}));
}

function showConfirm(message, options) {
    return showAppDialog(Object.assign({message, confirm: true}, options || {}))
        .then((result) => !!result);
}

// ============ Dashboard ============

async function refreshStats() {
    try {
        const data = await apiGetCached('/api/stats', applyStats);
        applyStats(data);
    } catch (e) {
        setStatus(false);
    }
}

function applyStats(data) {
    setText('tasks-total', data.tasks_total);
    setText('tasks-active', data.tasks_active);
    setText('tasks-running', data.tasks_running);
    setText('total-sent', data.total_sent);
    setText('plugins-count', data.plugins_count || '—');
    setText('last-start', data.last_start || '—');
    setText('uptime', data.uptime || '—');
    setText('set-tasks-total', data.tasks_total);
    setText('set-tasks-active', data.tasks_active);
    setText('set-tasks-running', data.tasks_running);
    setText('set-total-sent', data.total_sent);
    setStatus(true);
}

async function refreshBot() {
    try {
        const data = await apiGetCached('/api/bot', applyBot);
        applyBot(data);
    } catch (e) {
        console.error('Bot info error:', e);
    }
}

function applyBot(data) {
    setText('bot-name', data.name || '—');
    setText('bot-username', data.username || '—');
    setText('bot-id', data.user_id || '—');
    setText('bot-phone', data.phone || '—');
    setText('bot-session', data.session || '—');
    setText('tg-name', data.name || '—');
    setText('tg-username', data.username || '—');
    setText('tg-id', data.user_id || '—');
}

function setStatus(online) {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const dotInline = document.getElementById('status-dot-inline');
    const textInline = document.getElementById('status-text-inline');
    const status = tr(online ? 'web.common.online' : 'web.common.offline');
    
    if (online) {
        if (dot) dot.classList.remove('offline');
        if (dotInline) dotInline.classList.remove('offline');
    } else {
        if (dot) dot.classList.add('offline');
        if (dotInline) dotInline.classList.add('offline');
    }
    
    if (text) text.textContent = status;
    if (textInline) textInline.textContent = status;
}

// ============ Tasks ============

let tasks = [];
let currentFilter = 'all';

async function loadTasks() {
    const list = document.getElementById('tasks-list');
    const loadingTimer = scheduleLoading(list);
    try {
        const data = await apiGetCached('/api/tasks', applyTasks);
        applyTasks(data);
    } catch (e) {
        console.error('Load tasks error:', e);
        if (list) {
            list.innerHTML = `<div class="empty"><div class="empty-text">${tr('web.common.error', {error: e.message})}</div></div>`;
        }
    } finally {
        if (loadingTimer) clearTimeout(loadingTimer);
        if (list) list.setAttribute('aria-busy', 'false');
    }
}

function applyTasks(data) {
    if (!data || !Array.isArray(data.tasks)) return;
    tasks = data.tasks;
    updateCounts();
    renderTasks();
}

function updateCounts() {
    setText('count-all', tasks.length);
    setText('count-active', tasks.filter(t => t.active).length);
    setText('count-running', tasks.filter(t => t.running).length);
    setText('count-stopped', tasks.filter(t => !t.active).length);
}

function renderTasks() {
    const list = document.getElementById('tasks-list');
    if (!list) return;
    
    let filtered = tasks;
    if (currentFilter === 'active') filtered = tasks.filter(t => t.active);
    else if (currentFilter === 'running') filtered = tasks.filter(t => t.running);
    else if (currentFilter === 'stopped') filtered = tasks.filter(t => !t.active);
    
    if (filtered.length === 0) {
        list.innerHTML = `<div class="empty"><div class="empty-text">${tr('web.tasks.empty')}</div></div>`;
        return;
    }
    
    list.innerHTML = filtered.map(t => {
        const statusBadge = t.running
            ? `<span class="badge badge-success">${tr('web.tasks.status_running')}</span>`
            : t.active
                ? `<span class="badge badge-warning">${tr('web.tasks.status_active')}</span>`
                : `<span class="badge badge-muted">${tr('web.tasks.status_stopped')}</span>`;
        
        const onceBadge = t.is_once
            ? `<span class="badge badge-info">${tr('web.tasks.badge_once', {time: t.once_time || ''})}</span>`
            : '';
        
        const rotationBadge = t.rotation_count > 0
            ? `<span class="badge badge-info">${tr('web.tasks.badge_rotation', {count: t.rotation_count})}</span>`
            : '';
        
        const mediaBadge = t.media_path
            ? `<span class="badge badge-info">${tr('web.tasks.media_' + (t.media_type || 'none'))}</span>`
            : '';
        
        const chatDisplay = t.chats.length > 1
            ? `${t.chats[0]} +${t.chats.length - 1}`
            : (t.chats[0] || t.chat);
        
        let actionBtns = '';
        
        if (t.active) {
            if (t.running) {
                actionBtns += `<button class="btn btn-sm" onclick="stopTask(${t.id})">${tr('web.tasks.stop')}</button>`;
            } else {
                actionBtns += `<button class="btn btn-sm btn-success" onclick="startTask(${t.id})">${tr('web.tasks.start')}</button>`;
            }
            actionBtns += `<button class="btn btn-sm btn-danger" onclick="deactivateTask(${t.id})">${tr('web.tasks.deactivate_short')}</button>`;
        } else {
            actionBtns += `<button class="btn btn-sm btn-success" onclick="activateTask(${t.id})">${tr('web.tasks.activate')}</button>`;
        }
        
        return `
        <div class="task-item">
            <div class="task-main">
                <div class="task-title">
                    #${t.id} ${statusBadge} ${onceBadge} ${rotationBadge} ${mediaBadge}
                </div>
                <div class="task-chat">${escapeHtml(chatDisplay)}</div>
                ${t.message ? `<div class="task-message">${escapeHtml(t.message)}</div>` : ''}
                <div class="task-meta">
                    <span class="task-meta-item">${CLOCK_ICON}${escapeHtml(t.interval)}</span>
                    <span class="task-meta-item">${SEND_ICON}${tr('web.tasks.sent_count', {count: t.total_sent})}</span>
                    ${t.last_sent ? `<span class="task-meta-item">${HISTORY_ICON}${escapeHtml(t.last_sent)}</span>` : ''}
                </div>
            </div>
            <div class="task-actions">
                ${actionBtns}
                <button class="btn btn-sm" onclick="editTask(${t.id})">${tr('web.tasks.edit_short')}</button>
                <button class="btn btn-sm btn-danger" onclick="deleteTask(${t.id})">${tr('web.tasks.delete_short')}</button>
            </div>
        </div>
        `;
    }).join('');

    refreshIcons(list);
}

async function startTask(id) {
    try {
        await apiPost(`/api/tasks/${id}/start`);
        await loadTasks();
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

async function stopTask(id) {
    try {
        await apiPost(`/api/tasks/${id}/stop`);
        await loadTasks();
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

async function activateTask(id) {
    try {
        await apiPost(`/api/tasks/${id}/activate`);
        await loadTasks();
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

async function deactivateTask(id) {
    if (!await showConfirm(tr('web.tasks.confirm_deactivate', {id}, true), {danger: true})) return;
    try {
        await apiPost(`/api/tasks/${id}/deactivate`);
        await loadTasks();
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

async function deleteTask(id) {
    if (!await showConfirm(tr('web.tasks.confirm_delete', {id}, true), {danger: true})) return;
    try {
        await apiDelete(`/api/tasks/${id}`);
        await loadTasks();
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

async function stopAllTasks() {
    if (tasks.filter(t => t.running).length === 0) {
        await showAlert(tr('web.tasks.no_running', null, true));
        return;
    }
    
    try {
        await apiPost('/api/tasks/stop-all');
        await loadTasks();
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

async function deactivateAllTasks() {
    if (tasks.filter(t => t.active).length === 0) {
        await showAlert(tr('web.tasks.no_active', null, true));
        return;
    }
    
    if (!await showConfirm(tr('web.tasks.confirm_deactivate_all', null, true), {danger: true})) return;
    
    try {
        await apiPost('/api/tasks/deactivate-all');
        await loadTasks();
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

// ============ Task Modal ============

function openCreateTaskModal() {
    document.getElementById('task-id').value = '';
    document.getElementById('task-chat').value = '';
    document.getElementById('task-message').value = '';
    document.getElementById('task-message').disabled = false;
    document.getElementById('task-message').style.opacity = '1';
    document.getElementById('task-interval').value = '60';
    document.getElementById('task-media-type').value = '';
    document.getElementById('task-media-path').value = '';
    document.getElementById('task-once-time').value = '';

    const mediaFile = document.getElementById('task-media-file');
    if (mediaFile) mediaFile.value = '';
    const uploadStatus = document.getElementById('media-upload-status');
    if (uploadStatus) uploadStatus.textContent = tr('web.tasks.media_upload_hint', null, true);
    
    const rotationCheckbox = document.getElementById('task-rotation');
    const onceCheckbox = document.getElementById('task-once');
    rotationCheckbox.checked = false;
    rotationCheckbox.disabled = false;
    onceCheckbox.checked = false;
    onceCheckbox.disabled = false;
    
    document.getElementById('task-rotation-messages').value = '';
    
    const rotationContainer = document.getElementById('rotation-container');
    const onceContainer = document.getElementById('once-container');
    if (rotationContainer) rotationContainer.style.opacity = '1';
    if (onceContainer) onceContainer.style.opacity = '1';
    
    document.getElementById('modal-title').textContent = tr('web.tasks.new_task', null, true);
    
    document.getElementById('rotation-field').style.display = 'none';
    document.getElementById('once-field').style.display = 'none';
    document.getElementById('media-path-field').style.display = 'none';
    document.getElementById('interval-field').style.display = 'block';
    document.getElementById('message-hint').style.display = 'none';
    
    document.getElementById('task-modal').classList.add('open');
}

async function editTask(id) {
    try {
        const data = await apiGet(`/api/tasks/${id}`);
        
        document.getElementById('task-id').value = id;
        
        document.getElementById('task-rotation').disabled = false;
        document.getElementById('task-once').disabled = false;
        document.getElementById('task-message').disabled = false;
        document.getElementById('task-message').style.opacity = '1';
        
        const rotationContainer = document.getElementById('rotation-container');
        const onceContainer = document.getElementById('once-container');
        if (rotationContainer) rotationContainer.style.opacity = '1';
        if (onceContainer) onceContainer.style.opacity = '1';
        
        let chatsStr = '';
        if (data.chats_list && data.chats_list.length > 0) {
            chatsStr = data.chats_list.join(', ');
        } else {
            chatsStr = data.chat || '';
        }
        document.getElementById('task-chat').value = chatsStr;
        
        document.getElementById('task-message').value = data.message || '';
        
        if (data.media_path) {
            document.getElementById('task-media-type').value = data.media_type || 'photo';
            document.getElementById('task-media-path').value = data.media_path;
            document.getElementById('media-path-field').style.display = 'block';
        } else {
            document.getElementById('task-media-type').value = '';
            document.getElementById('task-media-path').value = '';
            document.getElementById('media-path-field').style.display = 'none';
        }

        const mediaFile = document.getElementById('task-media-file');
        if (mediaFile) mediaFile.value = '';
        const uploadStatus = document.getElementById('media-upload-status');
        if (uploadStatus) uploadStatus.textContent = tr('web.tasks.media_upload_hint', null, true);
        
        const msgList = data.messages_list || [];
        const rotationCheckbox = document.getElementById('task-rotation');
        if (msgList.length > 0) {
            rotationCheckbox.checked = true;
            document.getElementById('task-rotation-messages').value = msgList.join('\n===\n');
        } else {
            rotationCheckbox.checked = false;
            document.getElementById('task-rotation-messages').value = '';
        }
        
        const onceCheckbox = document.getElementById('task-once');
        if (data.is_once && msgList.length === 0) {
            onceCheckbox.checked = true;
            document.getElementById('task-once-time').value = data.once_time || '';
        } else {
            onceCheckbox.checked = false;
            document.getElementById('task-once-time').value = '';
        }
        
        const cfg = data.interval_config || {};
        let intervalStr = '';
        if (cfg.type === 'interval') {
            intervalStr = String(cfg.seconds);
        } else if (cfg.type === 'random') {
            intervalStr = `${cfg.min}-${cfg.max}`;
        } else if (cfg.type === 'schedule_multi') {
            const times = (cfg.times || []).map(t => 
                `${String(t.hour).padStart(2,'0')}:${String(t.minute).padStart(2,'0')}`
            ).join(',');
            const days = (cfg.days || []).join(',');
            intervalStr = days ? `${times}@${days}` : times;
        }
        document.getElementById('task-interval').value = intervalStr;
        
        document.getElementById('modal-title').textContent = tr('web.tasks.edit_task', {id}, true);
        
        toggleRotation();
        toggleOnce();
        
        document.getElementById('task-modal').classList.add('open');
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

function closeTaskModal() {
    document.getElementById('task-modal').classList.remove('open');
}

function toggleMediaPath() {
    const type = document.getElementById('task-media-type').value;
    const field = document.getElementById('media-path-field');
    if (field) {
        field.style.display = type ? 'block' : 'none';
    }
}

function uploadTaskMedia(event) {
    const input = event.target;
    const file = input.files && input.files[0];
    if (!file) return;

    const status = document.getElementById('media-upload-status');
    const button = document.getElementById('task-media-upload-btn');
    const pathInput = document.getElementById('task-media-path');
    const typeSelect = document.getElementById('task-media-type');

    if (status) status.textContent = tr('web.tasks.media_uploading', null, true);
    if (button) button.disabled = true;

    const form = new FormData();
    form.append('file', file);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/media/upload');

    xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && status) {
            const percent = Math.round((e.loaded / e.total) * 100);
            status.textContent = `${tr('web.tasks.media_uploading', null, true)} ${percent}%`;
        }
    };

    xhr.onload = async () => {
        if (button) button.disabled = false;
        input.value = '';

        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) {}

        if (xhr.status >= 200 && xhr.status < 300 && data && data.path) {
            if (pathInput) pathInput.value = data.path;
            if (typeSelect && !typeSelect.value && data.media_type) {
                typeSelect.value = data.media_type;
                toggleMediaPath();
            }
            if (status) status.textContent = tr('web.tasks.media_uploaded', {name: data.name}, true);
            return;
        }

        const message = (data && data.error) || `HTTP ${xhr.status}`;
        if (status) status.textContent = tr('web.tasks.media_upload_hint', null, true);
        await showAlert(tr('web.tasks.media_upload_error', {error: message}, true));
    };

    xhr.onerror = async () => {
        if (button) button.disabled = false;
        input.value = '';
        if (status) status.textContent = tr('web.tasks.media_upload_hint', null, true);
        await showAlert(tr('web.tasks.media_upload_error', {error: tr('web.common.unknown_error')}, true));
    };

    xhr.send(form);
}

function toggleRotation() {
    const checked = document.getElementById('task-rotation').checked;
    const rotationField = document.getElementById('rotation-field');
    const messageField = document.getElementById('task-message');
    const messageHint = document.getElementById('message-hint');
    const onceCheckbox = document.getElementById('task-once');
    const onceContainer = document.getElementById('once-container');
    const intervalField = document.getElementById('interval-field');
    const onceField = document.getElementById('once-field');
    
    if (rotationField) {
        rotationField.style.display = checked ? 'block' : 'none';
    }
    
    if (messageField) {
        if (checked) {
            messageField.disabled = true;
            messageField.value = '';
            messageField.style.opacity = '0.5';
        } else {
            messageField.disabled = false;
            messageField.style.opacity = '1';
        }
    }
    
    if (messageHint) {
        messageHint.style.display = checked ? 'block' : 'none';
    }
    
    if (onceCheckbox && onceContainer) {
        if (checked) {
            onceCheckbox.checked = false;
            onceCheckbox.disabled = true;
            onceContainer.style.opacity = '0.5';
            if (onceField) onceField.style.display = 'none';
            if (intervalField) intervalField.style.display = 'block';
        } else {
            onceCheckbox.disabled = false;
            onceContainer.style.opacity = '1';
        }
    }
}

function toggleOnce() {
    const checked = document.getElementById('task-once').checked;
    const onceField = document.getElementById('once-field');
    const intervalField = document.getElementById('interval-field');
    const rotationCheckbox = document.getElementById('task-rotation');
    const rotationContainer = document.getElementById('rotation-container');
    const rotationField = document.getElementById('rotation-field');
    const messageField = document.getElementById('task-message');
    const messageHint = document.getElementById('message-hint');
    
    if (onceField) {
        onceField.style.display = checked ? 'block' : 'none';
    }
    
    if (intervalField) {
        intervalField.style.display = checked ? 'none' : 'block';
    }
    
    if (rotationCheckbox && rotationContainer) {
        if (checked) {
            rotationCheckbox.checked = false;
            rotationCheckbox.disabled = true;
            rotationContainer.style.opacity = '0.5';
            if (rotationField) rotationField.style.display = 'none';
            
            if (messageField) {
                messageField.disabled = false;
                messageField.style.opacity = '1';
            }
            if (messageHint) messageHint.style.display = 'none';
        } else {
            rotationCheckbox.disabled = false;
            rotationContainer.style.opacity = '1';
        }
    }
}

async function saveTask(event) {
    event.preventDefault();
    
    const id = document.getElementById('task-id').value;
    
    const chatInput = document.getElementById('task-chat').value.trim();
    const message = document.getElementById('task-message').value.trim();
    const mediaType = document.getElementById('task-media-type').value;
    const mediaPath = document.getElementById('task-media-path').value.trim();
    const useRotation = document.getElementById('task-rotation').checked;
    const rotationMessages = document.getElementById('task-rotation-messages').value;
    const isOnce = document.getElementById('task-once').checked;
    const onceTime = document.getElementById('task-once-time').value.trim();
    const interval = document.getElementById('task-interval').value.trim();
    
    const chats = chatInput.split(',').map(c => c.trim()).filter(c => c);
    if (chats.length === 0) {
        await showAlert(tr('web.tasks.error_no_chats', null, true));
        return;
    }
    
    let messagesList = null;
    if (useRotation && rotationMessages.trim()) {
        messagesList = rotationMessages.split('===').map(m => m.trim()).filter(m => m);
        if (messagesList.length < 2) {
            await showAlert(tr('web.tasks.error_rotation_min', null, true));
            return;
        }
    }
    
    if (useRotation) {
        if (!messagesList) {
            await showAlert(tr('web.tasks.error_rotation_empty', null, true));
            return;
        }
    } else {
        if (!message && !mediaPath) {
            await showAlert(tr('web.tasks.error_no_content', null, true));
            return;
        }
    }
    
    if (isOnce && !onceTime) {
        await showAlert(tr('web.tasks.error_once_time', null, true));
        return;
    }
    
    const data = {
        chat: chats[0],
        chats: chats,
        message: useRotation ? '' : message,
        media_type: mediaType || null,
        media_path: mediaPath || null,
        messages_list: messagesList,
        is_once: isOnce ? 1 : 0,
        once_time: isOnce ? onceTime : null,
        interval: interval,
    };
    
    try {
        let taskId = id;
        
        if (id) {
            await apiPut(`/api/tasks/${id}`, data);
        } else {
            const result = await apiPost('/api/tasks', data);
            taskId = result.id;
            
            if (!isOnce) {
                const runNow = await showConfirm(tr('web.tasks.created_run_now', {id: taskId}, true));
                if (runNow) {
                    await apiPost(`/api/tasks/${taskId}/start`);
                }
            }
        }
        
        closeTaskModal();
        await loadTasks();
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

function setupTaskFilters() {
    const filters = document.querySelectorAll('#filters .filter-chip');
    filters.forEach(btn => {
        btn.addEventListener('click', () => {
            filters.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentFilter = btn.dataset.filter;
            renderTasks();
        });
    });
}

// ============ Plugins ============

let plugins = [];
let currentPluginFilter = 'all';

async function loadPlugins() {
    const list = document.getElementById('plugins-list');
    const loadingTimer = scheduleLoading(list);
    try {
        const data = await apiGetCached('/api/plugins', applyPlugins);
        applyPlugins(data);
    } catch (e) {
        console.error('Load plugins error:', e);
        if (list) {
            list.innerHTML = `<div class="empty"><div class="empty-text">${tr('web.common.error', {error: e.message})}</div></div>`;
        }
    } finally {
        if (loadingTimer) clearTimeout(loadingTimer);
        if (list) list.setAttribute('aria-busy', 'false');
    }
}

function applyPlugins(data) {
    if (!data || !Array.isArray(data.plugins)) return;
    plugins = data.plugins;
    updatePluginCounts();
    renderPlugins();
}

function updatePluginCounts() {
    setText('count-all', plugins.length);
    setText('count-loaded', plugins.filter(p => p.loaded).length);
    setText('count-unloaded', plugins.filter(p => !p.loaded).length);
}

function renderPlugins() {
    const list = document.getElementById('plugins-list');
    if (!list) return;
    
    let filtered = plugins;
    if (currentPluginFilter === 'loaded') filtered = plugins.filter(p => p.loaded);
    else if (currentPluginFilter === 'unloaded') filtered = plugins.filter(p => !p.loaded);
    
    if (filtered.length === 0) {
        list.innerHTML = `<div class="empty"><div class="empty-text">${tr('web.plugins.empty')}</div></div>`;
        return;
    }
    
    list.innerHTML = filtered.map(p => {
        const statusBadge = p.loaded
            ? `<span class="badge badge-success">${tr('web.plugins.status_loaded')}</span>`
            : `<span class="badge badge-muted">${tr('web.plugins.status_unloaded')}</span>`;
        
        const categoryName = p.category === 'General' ? tr('web.plugins.category_default') : p.category;
        const categoryBadge = categoryName
            ? `<span class="badge badge-info">${escapeHtml(categoryName)}</span>`
            : '';
        
        let handlersText = '';
        if (p.loaded) {
            const parts = [];
            if (p.handlers.commands > 0) parts.push(tr('web.plugins.handlers_commands', {count: p.handlers.commands}));
            if (p.handlers.watchers > 0) parts.push(tr('web.plugins.handlers_watchers', {count: p.handlers.watchers}));
            if (p.handlers.loops > 0) parts.push(tr('web.plugins.handlers_loops', {count: p.handlers.loops}));
            if (parts.length > 0) {
                handlersText = `<div class="plugin-handlers">${parts.join(' · ')}</div>`;
            }
        }
        
        const pagesHtml = (p.pages && p.pages.length)
            ? `<div class="plugin-pages">${p.pages.map(page =>
                `<span class="plugin-page-group"><a class="btn btn-sm" href="${escapeHtml(page.url)}">${escapeHtml(page.title)}</a>${pluginPinButton(p.name, page)}</span>`
            ).join('')}</div>`
            : '';
        
        let actionBtns = '';
        
        if (p.loaded) {
            actionBtns += `<button class="btn btn-sm" onclick="reloadPlugin('${p.name}')">${tr('web.plugins.reload')}</button>`;
            actionBtns += `<button class="btn btn-sm btn-danger" onclick="unloadPlugin('${p.name}')">${tr('web.plugins.unload')}</button>`;
        } else {
            actionBtns += `<button class="btn btn-sm btn-success" onclick="loadPluginByName('${p.name}')">${tr('web.plugins.load')}</button>`;
        }
        
        actionBtns += `<button class="btn btn-sm btn-danger" onclick="deletePlugin('${p.name}')">${tr('web.plugins.delete')}</button>`;
        
        return `
        <div class="plugin-item">
            <div class="plugin-main">
                <div class="plugin-title">
                    ${escapeHtml(p.name)}
                    <span class="plugin-version">v${escapeHtml(p.version)}</span>
                    ${statusBadge}
                    ${categoryBadge}
                </div>
                <div class="plugin-meta">
                    <span>${escapeHtml(p.author)}</span>
                    <span>${escapeHtml(p.id)}</span>
                </div>
                ${p.description ? `<div class="plugin-description">${escapeHtml(p.description)}</div>` : ''}
                ${handlersText}
                ${pagesHtml}
            </div>
            <div class="plugin-actions">
                ${actionBtns}
            </div>
        </div>
        `;
    }).join('');

    refreshIcons(list);
}

async function loadPluginByName(name) {
    try {
        await apiPost(`/api/plugins/${name}/load`);
        await loadPlugins();
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

async function unloadPlugin(name) {
    if (!await showConfirm(tr('web.plugins.confirm_unload', {name}, true), {danger: true})) return;
    try {
        await apiPost(`/api/plugins/${name}/unload`);
        await loadPlugins();
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

async function reloadPlugin(name) {
    try {
        await apiPost(`/api/plugins/${name}/reload`);
        await loadPlugins();
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

async function deletePlugin(name) {
    if (!await showConfirm(tr('web.plugins.confirm_delete', {name}, true), {danger: true})) return;
    try {
        await apiDelete(`/api/plugins/${name}`);
        await loadPlugins();
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

function setupPluginFilters() {
    const filters = document.querySelectorAll('#filters .filter-chip');
    filters.forEach(btn => {
        btn.addEventListener('click', () => {
            filters.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentPluginFilter = btn.dataset.filter;
            renderPlugins();
        });
    });
}

function svgIcon(name, className) {
    const classes = `lucide lucide-${name}${className ? ' ' + className : ''}`;
    return `<svg class="${classes}" xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;
}

const PIN_ICON = svgIcon('pin');
const CLOCK_ICON = svgIcon('clock', 'task-meta-icon');
const SEND_ICON = svgIcon('send', 'task-meta-icon');
const HISTORY_ICON = svgIcon('history', 'task-meta-icon');

function refreshIcons(root) {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons({ root: root || document });
    }
}

function pluginPinButton(pluginName, page) {
    const pinned = !!page.pinned;
    const label = tr(pinned ? 'web.plugins.unpin' : 'web.plugins.pin');
    return `<button type="button" class="btn btn-sm plugin-pin-btn ${pinned ? 'active' : ''}" data-plugin="${escapeHtml(pluginName)}" data-path="${escapeHtml(page.path)}" data-pinned="${pinned}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}" aria-pressed="${pinned}">${PIN_ICON}</button>`;
}

function openPluginPagesModal() {
    renderPluginPagesModal();
    document.getElementById('plugin-pages-modal').classList.add('open');
}

function closePluginPagesModal() {
    document.getElementById('plugin-pages-modal').classList.remove('open');
}

function renderPluginPagesModal() {
    const list = document.getElementById('plugin-pages-list');
    if (!list) return;

    const rows = [];
    plugins.filter(plugin => plugin.loaded).forEach(plugin => {
        (plugin.pages || []).forEach(page => rows.push({plugin, page}));
    });

    if (rows.length === 0) {
        list.innerHTML = `<div class="empty"><div class="empty-text">${escapeHtml(tr('web.plugins.pages_empty'))}</div></div>`;
        return;
    }

    list.innerHTML = `<div class="plugin-pages-list">${rows.map(({plugin, page}) => `
        <div class="plugin-page-row">
            <div class="plugin-page-row-main">
                <div class="plugin-page-row-title"><a href="${escapeHtml(page.url)}">${escapeHtml(page.title)}</a></div>
                <div class="plugin-page-row-meta">${escapeHtml(plugin.name)} · ${escapeHtml(page.path)}</div>
            </div>
            <div class="plugin-page-row-actions">
                ${pluginPinButton(plugin.name, page)}
            </div>
        </div>`).join('')}</div>`;

    refreshIcons(list);
}

async function setPluginPagePinned(pluginName, pagePath, pinned) {
    try {
        const result = await apiPost('/api/plugin-pages/pin', {
            plugin: pluginName,
            path: pagePath,
            pinned,
        });

        const plugin = plugins.find(item => item.name === pluginName);
        const page = plugin && (plugin.pages || []).find(item => item.path === pagePath);
        if (page) page.pinned = !!result.pinned;

        updatePinnedNavItem(plugin, page, !!result.pinned);
        renderPlugins();
        if (document.getElementById('plugin-pages-modal')?.classList.contains('open')) {
            renderPluginPagesModal();
        }
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

function pluginNavPath(page) {
    // Server-rendered nav items store the full URL path, while the API also
    // exposes the raw page path. Compare against the same full path.
    try {
        return new URL(page.url, window.location.origin).pathname;
    } catch (e) {
        return page.path;
    }
}

function renderNavIcon(container, value) {
    const icon = String(value || '').trim();
    container.textContent = '';
    if (!icon) {
        container.innerHTML = PIN_ICON;
        return;
    }
    if (icon.startsWith('<svg')) {
        // Plugins are trusted code; allow an inline SVG icon.
        container.innerHTML = icon;
        return;
    }
    if (/^(https?:\/\/|\/|data:image\/)/i.test(icon)) {
        const img = document.createElement('img');
        img.src = icon;
        img.alt = '';
        container.appendChild(img);
        return;
    }
    if (/[^\x00-\x7F]/.test(icon)) {
        const emoji = document.createElement('span');
        emoji.className = 'nav-item-emoji';
        emoji.textContent = icon;
        container.appendChild(emoji);
        return;
    }
    const lucide = document.createElement('i');
    lucide.setAttribute('data-lucide', icon);
    container.appendChild(lucide);
}

function updatePinnedNavItem(plugin, page, pinned) {
    if (!plugin || !page) return;

    const nav = document.querySelector('.bottom-nav .bottom-nav-inner');
    if (!nav) return;

    const pagePath = pluginNavPath(page);
    const existing = Array.from(nav.querySelectorAll('.plugin-pinned-nav-item')).find(item =>
        item.dataset.plugin === plugin.name && item.dataset.pagePath === pagePath
    );

    if (!pinned) {
        if (existing) existing.remove();
        return;
    }
    if (existing) return;

    const link = document.createElement('a');
    link.href = page.url;
    link.className = 'nav-item plugin-pinned-nav-item';
    link.dataset.plugin = plugin.name;
    link.dataset.pagePath = pagePath;
    link.title = `${plugin.name} — ${page.title}`;

    if (pagePath === window.location.pathname) {
        link.classList.add('active');
    }

    const icon = document.createElement('span');
    icon.className = 'nav-item-icon';
    renderNavIcon(icon, page.icon);

    const label = document.createElement('span');
    label.className = 'nav-item-label';
    label.textContent = page.title;

    link.append(icon, label);
    nav.appendChild(link);

    refreshIcons(link);
}

document.addEventListener('click', async (event) => {
    const button = event.target.closest('.plugin-pin-btn');
    if (!button || button.disabled) return;

    event.preventDefault();
    button.disabled = true;
    try {
        await setPluginPagePinned(
            button.dataset.plugin,
            button.dataset.path,
            button.dataset.pinned !== 'true'
        );
    } finally {
        button.disabled = false;
    }
});

// ============ Install Plugin Modal ============

function openInstallPluginModal() {
    document.getElementById('install-url').value = '';
    document.getElementById('install-progress').style.display = 'none';
    document.getElementById('install-btn').disabled = false;
    document.getElementById('install-modal').classList.add('open');
}

function closeInstallPluginModal() {
    document.getElementById('install-modal').classList.remove('open');
}

async function installPlugin() {
    const url = document.getElementById('install-url').value.trim();
    if (!url) {
        await showAlert(tr('web.plugins.error_url', null, true));
        return;
    }
    
    const btn = document.getElementById('install-btn');
    const progress = document.getElementById('install-progress');
    const progressText = document.getElementById('progress-text');
    const progressFill = document.getElementById('progress-fill');
    
    btn.disabled = true;
    progress.style.display = 'block';
    progressText.textContent = tr('web.plugins.cloning', null, true);
    progressFill.style.width = '30%';
    
    try {
        setTimeout(() => { progressFill.style.width = '60%'; }, 1000);
        setTimeout(() => { progressFill.style.width = '80%'; }, 3000);
        
        const result = await apiPost('/api/plugins/install', {url});
        
        progressFill.style.width = '100%';
        progressText.textContent = tr('web.plugins.installed', {name: result.name, version: result.version}, true);
        
        setTimeout(() => {
            closeInstallPluginModal();
            loadPlugins();
        }, 1000);
        
    } catch (e) {
        progress.style.display = 'none';
        btn.disabled = false;
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

// ============ Create Plugin Modal ============

function openCreatePluginModal() {
    document.getElementById('create-name').value = '';
    document.getElementById('create-author').value = '';
    document.getElementById('create-description').value = '';
    document.getElementById('create-category').value = tr('web.plugins.category_default');
    document.getElementById('create-web-page').checked = false;
    document.getElementById('create-modal').classList.add('open');
}

function closeCreatePluginModal() {
    document.getElementById('create-modal').classList.remove('open');
}

async function createPlugin() {
    const name = document.getElementById('create-name').value.trim();
    const author = document.getElementById('create-author').value.trim() || tr('web.common.unknown');
    const description = document.getElementById('create-description').value.trim() || tr('web.plugins.no_description');
    const category = document.getElementById('create-category').value.trim() || tr('web.plugins.category_default');
    const createWebPage = document.getElementById('create-web-page').checked;
    
    if (!name) {
        await showAlert(tr('web.plugins.error_name', null, true));
        return;
    }
    
    try {
        const result = await apiPost('/api/plugins/create', {name, author, description, category, create_web_page: createWebPage});
        closeCreatePluginModal();
        await loadPlugins();
        await showAlert(tr('web.plugins.created', {name: result.name, id: result.id, path: result.path}, true));
    } catch (e) {
        await showAlert(tr('web.common.error', {error: e.message}, true));
    }
}

// ============ Update Plugin Modal ============

function openUpdatePluginModal() {
    document.getElementById('update-name').value = '';
    document.getElementById('update-progress').style.display = 'none';
    document.getElementById('update-result').style.display = 'none';
    document.getElementById('update-btn').disabled = false;
    document.getElementById('update-modal').classList.add('open');
}

function closeUpdatePluginModal() {
    document.getElementById('update-modal').classList.remove('open');
}

async function updatePlugin(force = false) {
    const name = document.getElementById('update-name').value.trim();
    if (!name) {
        await showAlert(tr('web.plugins.error_plugin_name', null, true));
        return;
    }
    
    const btn = document.getElementById('update-btn');
    const progress = document.getElementById('update-progress');
    const progressText = document.getElementById('update-progress-text');
    const progressFill = document.getElementById('update-progress-fill');
    const result = document.getElementById('update-result');
    const resultText = document.getElementById('update-result-text');
    
    btn.disabled = true;
    progress.style.display = 'block';
    result.style.display = 'none';
    progressText.textContent = tr('web.plugins.checking', null, true);
    progressFill.style.width = '30%';
    
    try {
        setTimeout(() => {
            progressText.textContent = tr('web.plugins.pulling', null, true);
            progressFill.style.width = '60%';
        }, 1000);
        setTimeout(() => { progressFill.style.width = '80%'; }, 3000);
        
        const data = await apiPost(`/api/plugins/${name}/update`, {force});
        
        progressFill.style.width = '100%';
        progress.style.display = 'none';
        result.style.display = 'block';
        
        if (data.success) {
            resultText.textContent = data.message;
            resultText.style.color = 'var(--success)';
            await loadPlugins();
            setTimeout(closeUpdatePluginModal, 2000);
            
        } else if (data.no_changes) {
            resultText.textContent = data.message;
            resultText.style.color = 'var(--muted)';
            btn.disabled = false;
            
        } else if (data.newer_installed) {
            const confirmRollback = await showConfirm(tr('web.plugins.confirm_rollback', {
                old: data.old_version,
                new: data.new_version,
            }, true), {danger: true});
            
            if (confirmRollback) {
                await updatePlugin(true);
                return;
            } else {
                resultText.textContent = tr('web.plugins.cancelled', null, true);
                resultText.style.color = 'var(--muted)';
                btn.disabled = false;
            }
        } else {
            resultText.textContent = data.message || tr('web.plugins.updated', null, true);
            resultText.style.color = 'var(--muted)';
            btn.disabled = false;
        }
        
    } catch (e) {
        progress.style.display = 'none';
        result.style.display = 'block';
        resultText.textContent = tr('web.common.error', {error: e.message}, true);
        resultText.style.color = 'var(--accent)';
        btn.disabled = false;
    }
}

// ============ Logs ============

let logSocket = null;
let logsPaused = false;
let logsLimit = 500;
let allLogs = [];
let seenLogKeys = new Set();
let logStats = { DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0, CRITICAL: 0 };
let currentLevel = 'all';
let currentSearch = '';
let pollingInterval = null;
let usingPolling = false;
let lastTimestamp = null;
let logsScrollFrame = null;
let logsScrollCheckFrame = null;
let logsStatsFrame = null;
let loadingHistory = false;

const LOGS_LIMIT_STORAGE_KEY = 'telesender.logsLimit';
const LOGS_LIMIT_OPTIONS = [100, 500, 1000, 5000];

function restoreLogsLimit() {
    const select = document.getElementById('logs-limit');
    try {
        const saved = parseInt(localStorage.getItem(LOGS_LIMIT_STORAGE_KEY), 10);
        if (LOGS_LIMIT_OPTIONS.includes(saved)) {
            logsLimit = saved;
        }
    } catch (e) {}

    if (select) {
        select.value = String(logsLimit);
    }
}

function persistLogsLimit() {
    try {
        localStorage.setItem(LOGS_LIMIT_STORAGE_KEY, String(logsLimit));
    } catch (e) {}
}

function scheduleLogScroll() {
    if (logsPaused) {
        showScrollButton();
        return;
    }
    if (logsScrollFrame) return;

    logsScrollFrame = requestAnimationFrame(() => {
        logsScrollFrame = null;
        const container = document.getElementById('logs-container');
        if (container && !logsPaused) {
            container.scrollTop = container.scrollHeight;
            container.offsetHeight;
        }
    });
}

function initLogs() {
    const container = document.getElementById('logs-container');
    if (!container) return;
    
    restoreLogsLimit();
    currentLevel = 'all';
    document.querySelectorAll('#level-filters .filter-chip').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.level === 'all');
    });
    container.innerHTML = '';
    allLogs = [];
    seenLogKeys = new Set();
    logStats = { DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0, CRITICAL: 0 };
    
    loadExistingLogs().finally(connectLogs);
    setupLogsHandlers();
}

async function loadExistingLogs() {
    loadingHistory = true;
    try {
        const data = await apiGet(`/api/logs?limit=${logsLimit}`);
        if (data.logs && data.logs.length > 0) {
            data.logs.forEach(log => addLog(log));
            lastTimestamp = data.logs[data.logs.length - 1].timestamp;
        }
    } catch (e) {
        console.warn('Failed to load existing logs:', e);
    } finally {
        loadingHistory = false;
    }

    if (!logsPaused) {
        scheduleLogScroll();
    }
}

function connectLogs() {
    if (usingPolling) return;
    
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${window.location.host}/ws/logs`;
    
    let wsConnected = false;
    let fallbackTimeout = null;
    
    try {
        logSocket = new WebSocket(url);
    } catch (e) {
        console.error('WebSocket error:', e);
        startFallbackPolling();
        return;
    }
    
    fallbackTimeout = setTimeout(() => {
        if (!wsConnected) {
            console.warn('WebSocket timeout, switching to polling');
            if (logSocket) {
                try { logSocket.close(); } catch (e) {}
            }
            startFallbackPolling();
        }
    }, 5000);
    
    logSocket.onopen = () => {
        wsConnected = true;
        clearTimeout(fallbackTimeout);
        setWsStatus('connected');
        console.log('WebSocket connected');
    };
    
    logSocket.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            addLog(data);
        } catch (e) {
            console.error('Parse error:', e);
        }
    };
    
    logSocket.onclose = () => {
        clearTimeout(fallbackTimeout);
        
        if (!wsConnected) {
            startFallbackPolling();
        } else {
            setWsStatus('disconnected');
            setTimeout(() => {
                if (document.getElementById('logs-container')) {
                    connectLogs();
                }
            }, 3000);
        }
    };
    
    logSocket.onerror = () => {
        setWsStatus('disconnected');
    };
}

function startFallbackPolling() {
    if (pollingInterval) return;
    
    usingPolling = true;
    console.warn('Using AJAX polling');
    setWsStatus('connected');
    
    const poll = async () => {
        try {
            const url = lastTimestamp 
                ? `/api/logs?after=${encodeURIComponent(lastTimestamp)}`
                : `/api/logs?limit=${logsLimit}`;
            
            const data = await apiGet(url);
            
            if (data.logs && data.logs.length > 0) {
                data.logs.forEach(log => addLog(log));
                lastTimestamp = data.logs[data.logs.length - 1].timestamp;
            }
        } catch (e) {
            console.error('Polling error:', e);
            setWsStatus('disconnected');
        }
    };
    
    poll();
    pollingInterval = setInterval(poll, 2000);
}

function stopFallbackPolling() {
    if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
    }
    usingPolling = false;
}

function addLog(data) {
    if (!data.time || !data.level) return;
    
    const key = `${data.time}_${data.level}_${data.message}`;
    if (seenLogKeys.has(key)) {
        return;
    }
    seenLogKeys.add(key);
    
    allLogs.push(data);
    while (allLogs.length > logsLimit) {
        const removed = allLogs.shift();
        seenLogKeys.delete(`${removed.time}_${removed.level}_${removed.message}`);
    }
    
    const level = data.level === 'CRITICAL' ? 'ERROR' : data.level;
    if (logStats[level] !== undefined) {
        logStats[level]++;
    }
    scheduleStatsUpdate();
    
    if (passesFilter(data)) {
        const container = document.getElementById('logs-container');
        if (container) {
            const shouldAutoScroll = !loadingHistory && !logsPaused;

            if (container.querySelector('.log-placeholder')) {
                container.innerHTML = '';
            }
            
            container.appendChild(createLogElement(data));
            
            while (container.children.length > logsLimit) {
                container.removeChild(container.firstChild);
            }
            
            if (shouldAutoScroll) {
                scheduleLogScroll();
            }
        }
    }
}

function createLogElement(data) {
    const line = document.createElement('div');
    line.className = 'log-line';
    line.dataset.level = data.level === 'CRITICAL' ? 'ERROR' : data.level;
    
    const timeSpan = document.createElement('span');
    timeSpan.className = 'log-time';
    timeSpan.textContent = `[${data.time}] `;
    
    const levelSpan = document.createElement('span');
    levelSpan.className = `log-level-${data.level}`;
    levelSpan.textContent = `${data.level.padEnd(8)} `;
    
    const msgSpan = document.createElement('span');
    
    if (currentSearch) {
        const regex = new RegExp(`(${escapeRegex(currentSearch)})`, 'gi');
        msgSpan.innerHTML = escapeHtml(data.message).replace(regex, '<mark>$1</mark>');
    } else {
        msgSpan.textContent = data.message;
    }
    
    line.appendChild(timeSpan);
    line.appendChild(levelSpan);
    line.appendChild(msgSpan);
    
    return line;
}

function passesFilter(data) {
    const level = data.level === 'CRITICAL' ? 'ERROR' : data.level;
    
    if (currentLevel !== 'all' && level !== currentLevel) {
        return false;
    }
    
    if (currentSearch && !data.message.toLowerCase().includes(currentSearch.toLowerCase())) {
        return false;
    }
    
    return true;
}

function applyFilters() {
    currentSearch = document.getElementById('logs-search').value.trim();
    
    const container = document.getElementById('logs-container');
    if (!container) return;
    
    container.innerHTML = '';
    
    const filtered = allLogs.filter(passesFilter);
    
    if (filtered.length === 0) {
        container.innerHTML = `<div class="log-line log-placeholder" style="color: #666;">${tr('web.logs.no_match')}</div>`;
        return;
    }
    
    filtered.forEach(data => {
        container.appendChild(createLogElement(data));
    });
    
    scheduleLogScroll();
}

function setWsStatus(status) {
    const dot = document.getElementById('ws-status-dot');
    const text = document.getElementById('ws-status-text');
    
    if (!dot || !text) return;
    
    dot.classList.remove('connected', 'disconnected');
    
    if (status === 'connected') {
        dot.classList.add('connected');
        text.textContent = usingPolling ? tr('web.logs.polling', null, true) : tr('web.logs.connected', null, true);
    } else {
        dot.classList.add('disconnected');
        text.textContent = tr('web.logs.disconnected', null, true);
    }
}

function setupLogsHandlers() {
    const container = document.getElementById('logs-container');
    if (!container) return;
    
    container.addEventListener('scroll', () => {
        if (logsScrollCheckFrame) return;
        logsScrollCheckFrame = requestAnimationFrame(() => {
            logsScrollCheckFrame = null;
            const isAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 30;
            
            if (!isAtBottom && !logsPaused) {
                logsPaused = true;
                container.classList.add('paused');
                updatePauseButton();
            }
            
            if (isAtBottom) {
                hideScrollButton();
                if (logsPaused) {
                    logsPaused = false;
                    container.classList.remove('paused');
                    updatePauseButton();
                }
            } else {
                showScrollButton();
            }
        });
    });
    
    document.querySelectorAll('#level-filters .filter-chip').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#level-filters .filter-chip').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentLevel = btn.dataset.level;
            applyFilters();
        });
    });
}

function togglePause() {
    logsPaused = !logsPaused;
    const container = document.getElementById('logs-container');
    
    if (container) {
        if (logsPaused) {
            container.classList.add('paused');
        } else {
            container.classList.remove('paused');
            container.scrollTop = container.scrollHeight;
            hideScrollButton();
        }
    }
    
    updatePauseButton();
}

function updatePauseButton() {
    const btn = document.getElementById('pause-btn');
    if (btn) {
        btn.textContent = logsPaused ? tr('web.logs.resume', null, true) : tr('web.logs.pause', null, true);
    }
}

function clearLogs() {
    const container = document.getElementById('logs-container');
    if (container) {
        container.innerHTML = `<div class="log-line log-placeholder" style="color: #666;">${tr('web.logs.cleared')}</div>`;
    }
    allLogs = [];
    seenLogKeys = new Set();
    logStats = { DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0, CRITICAL: 0 };
    updateStats();
}

function scheduleStatsUpdate() {
    if (logsStatsFrame) return;
    logsStatsFrame = requestAnimationFrame(() => {
        logsStatsFrame = null;
        updateStats();
    });
}

function updateStats() {
    setText('stat-debug', logStats.DEBUG);
    setText('stat-info', logStats.INFO);
    setText('stat-warning', logStats.WARNING);
    setText('stat-error', logStats.ERROR + logStats.CRITICAL);
    
    const total = logStats.DEBUG + logStats.INFO + logStats.WARNING + logStats.ERROR + logStats.CRITICAL;
    setText('stat-total', total);
}

function updateLimit() {
    const select = document.getElementById('logs-limit');
    const value = parseInt(select.value, 10);
    if (!LOGS_LIMIT_OPTIONS.includes(value)) return;
    logsLimit = value;
    persistLogsLimit();
    
    while (allLogs.length > logsLimit) {
        const removed = allLogs.shift();
        seenLogKeys.delete(`${removed.time}_${removed.level}_${removed.message}`);
    }
    
    const container = document.getElementById('logs-container');
    while (container && container.children.length > logsLimit) {
        container.removeChild(container.firstChild);
    }
}

async function saveLogs() {
    if (allLogs.length === 0) {
        await showAlert(tr('web.logs.no_logs_to_save', null, true));
        return;
    }
    
    const lines = allLogs.map(data => 
        `[${data.time}] ${data.level.padEnd(8)} ${data.message}`
    );
    
    const content = lines.join('\n');
    const blob = new Blob([content], {type: 'text/plain;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    
    const a = document.createElement('a');
    a.href = url;
    a.download = `logs_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function scrollToBottom() {
    const container = document.getElementById('logs-container');
    if (container) {
        container.style.webkitOverflowScrolling = 'touch';
        container.scrollTop = container.scrollHeight;
        requestAnimationFrame(() => {
            container.scrollTop = container.scrollHeight;
            container.offsetHeight;
            requestAnimationFrame(() => {
                container.scrollTop = container.scrollHeight;
                container.offsetHeight;
            });
        });
        hideScrollButton();
        
        if (logsPaused) {
            logsPaused = false;
            container.classList.remove('paused');
            updatePauseButton();
        }
    }
}

function showScrollButton() {
    const btn = document.getElementById('scroll-btn');
    if (btn) btn.style.display = 'flex';
}

function hideScrollButton() {
    const btn = document.getElementById('scroll-btn');
    if (btn) btn.style.display = 'none';
}

function reconnect() {
    stopFallbackPolling();
    
    if (logSocket) {
        try { logSocket.close(); } catch (e) {}
    }
    
    setTimeout(() => {
        connectLogs();
    }, 500);
}

// ============ Settings ============

const LANGUAGE_NAMES = {
    uk: 'Українська', en: 'English', ru: 'Русский',
    pl: 'Polski', de: 'Deutsch', es: 'Español', fr: 'Français',
    pt: 'Português', tr: 'Türkçe', id: 'Indonesia',
    it: 'Italiano', ja: '日本語', ko: '한국어'
};

let availableLanguages = Object.keys(LANGUAGE_NAMES);
let currentLanguage = 'en';

function languageName(code) {
    // Keep labels short enough for the fixed-width language buttons.
    if (code === 'id') return 'Indonesia';
    return LANGUAGE_NAMES[code] || code;
}

function updateLanguageUI(code) {
    currentLanguage = code || 'en';
    document.documentElement.lang = currentLanguage;
    setText('lang-button-label', languageName(currentLanguage));
    setText('lang-hint', tr('web.settings.current', {lang: languageName(currentLanguage)}, true));
    const container = document.getElementById('language-options');
    if (container) {
        container.querySelectorAll('.language-option').forEach(btn => {
            btn.classList.toggle('btn-primary', btn.dataset.lang === currentLanguage);
        });
    }
}

function renderLanguageOptions() {
    const container = document.getElementById('language-options');
    if (!container) return;
    container.innerHTML = '';
    availableLanguages.forEach(code => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn language-option';
        btn.dataset.lang = code;
        btn.onclick = () => changeLanguage(code);
        btn.textContent = languageName(code);
        container.appendChild(btn);
    });
    updateLanguageUI(currentLanguage);
}

function openLanguageModal() {
    renderLanguageOptions();
    const modal = document.getElementById('language-modal');
    if (!modal) return;
    modal.classList.add('open');
    const first = modal.querySelector('.language-option.btn-primary') || modal.querySelector('.language-option');
    if (first) first.focus();
}

function closeLanguageModal() {
    const modal = document.getElementById('language-modal');
    if (modal) modal.classList.remove('open');
}

function setupLanguageModal() {
    const modal = document.getElementById('language-modal');
    if (!modal) return;
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && modal.classList.contains('open')) {
            event.preventDefault();
            closeLanguageModal();
        }
    });
}

async function loadSettings() {
    try {
        const settings = await apiGetCached('/api/settings', applySettingsInfo);
        applySettingsInfo(settings);
    } catch (e) {
        console.error('Settings error:', e);
    }

    try {
        const stats = await apiGetCached('/api/stats', applyStats);
        applyStats(stats);
    } catch (e) {
        console.error('Settings stats error:', e);
    }

    try {
        const bot = await apiGetCached('/api/bot', applyBot);
        applyBot(bot);
    } catch (e) {
        console.error('Settings bot error:', e);
    }
}

function applySettingsInfo(settings) {
    if (settings.language_names) Object.assign(LANGUAGE_NAMES, settings.language_names);
    if (Array.isArray(settings.languages) && settings.languages.length) {
        availableLanguages = settings.languages;
    }
    setText('web-host', settings.web_host || '—');
    setText('web-port', settings.web_port || '—');
    setText('web-uptime', settings.uptime || '—');
    updateLanguageUI(settings.language);
    if (typeof settings.web_auth_2fa !== 'undefined') {
        update2FAUI(settings.web_auth_2fa);
    }
}

let twoFactorBusy = false;

async function poll2FAStatus(requestId) {
    const deadline = Date.now() + 130000;
    while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        try {
            const res = await fetch('/api/settings/2fa/status?req_id=' + encodeURIComponent(requestId));
            if (!res.ok) continue;
            const data = await res.json();
            if (data.status && data.status !== 'pending') {
                return data.status;
            }
        } catch (e) {
            // Network hiccup — keep polling until the deadline.
        }
    }
    return 'expired';
}

async function toggle2FA() {
    const btn = document.getElementById('btn-toggle-2fa');
    if (!btn || twoFactorBusy) return;
    const isCurrentlyEnabled = btn.getAttribute('data-enabled') === 'true';
    const newState = !isCurrentlyEnabled;

    function applyState(enabled) {
        btn.setAttribute('data-enabled', enabled ? 'true' : 'false');
        btn.className = 'btn btn-sm ' + (enabled ? 'btn-primary' : 'btn-secondary');
        btn.textContent = tr(enabled ? 'web.settings.two_factor_enabled' : 'web.settings.two_factor_disabled', null, true);
    }

    twoFactorBusy = true;
    btn.disabled = true;
    try {
        const res = await apiPost('/api/settings/2fa', {enabled: newState});
        // The cached /api/settings response still holds the old 2FA state.
        clearApiCache('/api/settings');

        // Disabling 2FA requires confirmation from the owner's Telegram.
        if (res.pending && res.request_id) {
            btn.textContent = tr('web.settings.two_factor_waiting', null, true);
            const status = await poll2FAStatus(res.request_id);
            if (status === 'approved') {
                applyState(false);
                await showAlert(tr('web.settings.two_factor_alert_disabled', null, true));
            } else {
                applyState(isCurrentlyEnabled);
                await showAlert(tr(status === 'rejected'
                    ? 'web.settings.two_factor_confirm_rejected'
                    : 'web.settings.two_factor_confirm_expired', null, true));
            }
            return;
        }

        const enabled = !!res.enabled;
        applyState(enabled);
        await showAlert(tr(enabled ? 'web.settings.two_factor_alert_enabled' : 'web.settings.two_factor_alert_disabled', null, true));
    } catch (e) {
        applyState(isCurrentlyEnabled);
        await showAlert(tr('web.common.error', {error: e.message}, true));
    } finally {
        btn.disabled = false;
        twoFactorBusy = false;
    }
}

function update2FAUI(enabled) {
    // Don't let the 10s settings refresh overwrite the pending state while
    // we are waiting for Telegram confirmation.
    if (twoFactorBusy) return;
    const btn = document.getElementById('btn-toggle-2fa');
    if (!btn) return;
    btn.setAttribute('data-enabled', enabled ? 'true' : 'false');
    if (enabled) {
        btn.className = 'btn btn-sm btn-primary';
        btn.textContent = tr('web.settings.two_factor_enabled', null, true);
    } else {
        btn.className = 'btn btn-sm btn-secondary';
        btn.textContent = tr('web.settings.two_factor_disabled', null, true);
    }
}

async function changeLanguage(code) {
    try {
        const data = await apiPost('/api/settings/language', {language: code});
        closeLanguageModal();
        updateLanguageUI(data.language || code);
        if (data.warning) await showAlert(data.warning);
        window.location.reload();
    } catch (e) {
        await showAlert(tr('web.settings.language_error', {error: e.message}, true));
    }
}

async function exportTasks() {
    try {
        const res = await fetch('/api/settings/export');
        if (!res.ok) {
            const err = await res.json().catch(() => ({error: `HTTP ${res.status}`}));
            throw new Error(err.error || `HTTP ${res.status}`);
        }

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'tasks_backup.json';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    } catch (e) {
        await showAlert(tr('web.settings.export_error', {error: e.message}, true));
    }
}

async function importTasks(event) {
    const input = event.target;
    const file = input.files && input.files[0];
    if (!file) return;

    try {
        const parsed = JSON.parse(await file.text());
        const tasks = Array.isArray(parsed) ? parsed : parsed.tasks;
        if (!Array.isArray(tasks)) {
            throw new Error(tr('web.settings.import_expected_array'));
        }

        const result = await apiPost('/api/settings/import', {tasks});
        await showAlert(tr('web.settings.imported_count', {count: result.count}, true));
        await loadSettings();
    } catch (e) {
        await showAlert(tr('web.settings.import_error', {error: e.message}, true));
    } finally {
        input.value = '';
    }
}

async function clearMediaCache() {
    if (!await showConfirm(tr('web.settings.confirm_clear_cache', null, true), {danger: true})) return;

    try {
        const result = await apiPost('/api/settings/clear-media');
        await showAlert(tr('web.settings.cleared_count', {count: result.count}, true));
    } catch (e) {
        await showAlert(tr('web.settings.clear_cache_error', {error: e.message}, true));
    }
}

// ============ Init ============

function applyInitialData() {
    // The server embeds a snapshot of the current lists into the page, so the
    // first paint already shows real data. The API is still fetched afterwards
    // to revalidate it. Mark the URLs as applied so the older sessionStorage
    // copy does not overwrite this fresh snapshot with staler data.
    const initial = window.__INITIAL_DATA__;
    if (!initial) return;

    if (Array.isArray(initial.tasks)) {
        tasks = initial.tasks;
        apiCacheApplied.add('/api/tasks');
        updateCounts();
        renderTasks();
    }

    if (Array.isArray(initial.plugins)) {
        plugins = initial.plugins;
        apiCacheApplied.add('/api/plugins');
        updatePluginCounts();
        renderPlugins();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    applyInitialData();
    refreshIcons();
    setupAppDialog();
    setupLanguageModal();

    if (document.getElementById('tasks-total')) {
        refreshStats();
        refreshBot();
        setInterval(refreshStats, 5000);
        setInterval(refreshBot, 60000);
    }
    
    if (document.getElementById('tasks-list')) {
        loadTasks();
        setupTaskFilters();
        setInterval(loadTasks, 5000);
    }
    
    if (document.getElementById('plugins-list')) {
        loadPlugins();
        setupPluginFilters();
    }
    
    if (document.getElementById('logs-container')) {
        initLogs();
    }
    
    if (document.getElementById('set-tasks-total')) {
        loadSettings();
        setInterval(loadSettings, 10000);
    }
});

