(function () {
    const STORAGE_KEY = 'crm_global_log_history';
    const MAX_HISTORY = 8000;
    let button = null;
    let overlay = null;
    let body = null;
    let historyLoaded = false;
    let logHistory = [];
    let seenKeys = new Set();

    function ensureLogModal() {
        if (button && overlay && body) return;

        button = document.createElement('button');
        button.type = 'button';
        button.className = 'global-log-button';
        button.textContent = '查看日志';
        button.addEventListener('click', openGlobalLogModal);

        overlay = document.createElement('div');
        overlay.className = 'global-log-overlay';
        overlay.innerHTML = `
            <div class="global-log-modal" role="dialog" aria-modal="true" aria-label="详细日志">
                <div class="global-log-head">
                    <h3>详细日志</h3>
                    <div class="global-log-actions">
                        <button type="button" class="global-log-small" data-action="clear">清空</button>
                        <button type="button" class="global-log-close" data-action="close" aria-label="关闭">×</button>
                    </div>
                </div>
                <div class="global-log-body"><div class="dim">等待操作...</div></div>
            </div>
        `;
        body = overlay.querySelector('.global-log-body');
        overlay.addEventListener('click', event => {
            if (event.target === overlay || event.target.dataset.action === 'close') closeGlobalLogModal();
            if (event.target.dataset.action === 'clear') clearGlobalLog();
        });

        document.body.appendChild(button);
        document.body.appendChild(overlay);
        loadHistory();
        renderHistory();
    }

    function openGlobalLogModal() {
        ensureLogModal();
        overlay.classList.add('show');
    }

    function closeGlobalLogModal() {
        if (overlay) overlay.classList.remove('show');
    }

    function clearGlobalLog() {
        ensureLogModal();
        logHistory = [];
        seenKeys = new Set();
        persistHistory();
        body.innerHTML = '<div class="dim">等待操作...</div>';
    }

    function normalizeLevel(level) {
        return ['info', 'success', 'error', 'warn', 'dim'].includes(level) ? level : 'dim';
    }

    function loadHistory() {
        if (historyLoaded) return;
        historyLoaded = true;
        try {
            const rows = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '[]');
            logHistory = Array.isArray(rows) ? rows.slice(-MAX_HISTORY) : [];
        } catch (e) {
            logHistory = [];
        }
        seenKeys = new Set(logHistory.map(row => row.key).filter(Boolean));
    }

    function persistHistory() {
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(logHistory.slice(-MAX_HISTORY)));
        } catch (e) {}
    }

    function renderHistory() {
        ensureLogModal();
        body.innerHTML = '';
        if (!logHistory.length) {
            body.innerHTML = '<div class="dim">等待操作...</div>';
            return;
        }
        for (const row of logHistory) {
            appendLogLine(row.message, row.level, row.time);
        }
        body.scrollTop = body.scrollHeight;
    }

    function appendLogLine(message, level, time) {
        if (body.textContent.trim() === '等待操作...') body.innerHTML = '';
        const line = document.createElement('div');
        line.className = normalizeLevel(level || 'dim');
        line.textContent = `[${time || new Date().toLocaleTimeString()}] ${message || ''}`;
        body.appendChild(line);
        while (body.children.length > 8000) {
            body.removeChild(body.firstChild);
        }
        body.scrollTop = body.scrollHeight;
    }

    function appendGlobalLog(message, level, time, key) {
        ensureLogModal();
        loadHistory();
        const stamp = time || new Date().toLocaleTimeString();
        const normalizedLevel = normalizeLevel(level || 'dim');
        const dedupeKey = key || '';
        if (dedupeKey && seenKeys.has(dedupeKey)) return;
        if (dedupeKey) seenKeys.add(dedupeKey);
        const entry = {
            time: stamp,
            message: String(message || ''),
            level: normalizedLevel,
            key: dedupeKey,
        };
        logHistory.push(entry);
        if (logHistory.length > MAX_HISTORY) {
            const removed = logHistory.splice(0, logHistory.length - MAX_HISTORY);
            for (const row of removed) {
                if (row.key) seenKeys.delete(row.key);
            }
        }
        persistHistory();
        appendLogLine(entry.message, entry.level, entry.time);
    }

    function appendGlobalLogRow(row) {
        if (!row) return;
        const key = row.id
            ? `row:${row.id}:${row.time || ''}:${row.message || ''}`
            : `row:${row.time || ''}:${row.level || 'dim'}:${row.message || ''}`;
        appendGlobalLog(row.message || '', row.level || 'dim', row.time || '', key);
    }

    function replaceGlobalLogRows(rows) {
        ensureLogModal();
        if (!rows || !rows.length) return;
        rows.forEach(appendGlobalLogRow);
    }

    window.globalLogAppend = appendGlobalLog;
    window.globalLogAppendRow = appendGlobalLogRow;
    window.globalLogReplaceRows = replaceGlobalLogRows;
    window.globalLogClear = clearGlobalLog;
    window.openGlobalLogModal = openGlobalLogModal;
    window.closeGlobalLogModal = closeGlobalLogModal;

    document.addEventListener('DOMContentLoaded', ensureLogModal);
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeGlobalLogModal();
    });
})();
