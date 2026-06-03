(function () {
    let button = null;
    let overlay = null;
    let body = null;

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
        body.innerHTML = '<div class="dim">等待操作...</div>';
    }

    function normalizeLevel(level) {
        return ['info', 'success', 'error', 'warn', 'dim'].includes(level) ? level : 'dim';
    }

    function appendGlobalLog(message, level, time) {
        ensureLogModal();
        if (body.textContent.trim() === '等待操作...') body.innerHTML = '';
        const line = document.createElement('div');
        line.className = normalizeLevel(level || 'dim');
        const stamp = time || new Date().toLocaleTimeString();
        line.textContent = `[${stamp}] ${message || ''}`;
        body.appendChild(line);
        while (body.children.length > 8000) {
            body.removeChild(body.firstChild);
        }
        body.scrollTop = body.scrollHeight;
    }

    function appendGlobalLogRow(row) {
        if (!row) return;
        appendGlobalLog(row.message || '', row.level || 'dim', row.time || '');
    }

    function replaceGlobalLogRows(rows) {
        ensureLogModal();
        body.innerHTML = '';
        if (!rows || !rows.length) {
            body.innerHTML = '<div class="dim">等待操作...</div>';
            return;
        }
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
