(function () {
    const preferredFormats = [
        'code_128',
        'code_39',
        'ean_13',
        'ean_8',
        'upc_a',
        'upc_e',
        'itf',
        'codabar',
        'qr_code',
        'data_matrix'
    ];

    let activeScanner = null;

    function normalizeBarcode(value) {
        return String(value || '').replace(/\s+/g, '').trim().toUpperCase();
    }

    function cameraAllowedByContext() {
        return window.isSecureContext || ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname);
    }

    async function makeDetector() {
        if (!('BarcodeDetector' in window)) {
            throw new Error('当前浏览器不支持相机识别条码，请使用安卓 Chrome/Edge，或升级浏览器。');
        }
        if (typeof BarcodeDetector.getSupportedFormats !== 'function') {
            return new BarcodeDetector();
        }
        const supported = await BarcodeDetector.getSupportedFormats();
        const formats = preferredFormats.filter(format => supported.includes(format));
        return formats.length ? new BarcodeDetector({ formats }) : new BarcodeDetector();
    }

    function ensureOverlay() {
        let overlay = document.getElementById('barcodeScanOverlay');
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.id = 'barcodeScanOverlay';
        overlay.className = 'barcode-scan-overlay';
        overlay.innerHTML = `
            <div class="barcode-scan-panel" role="dialog" aria-modal="true" aria-labelledby="barcodeScanTitle">
                <div class="barcode-scan-head">
                    <div class="barcode-scan-title" id="barcodeScanTitle">扫码输入条码</div>
                    <button class="barcode-scan-close" type="button" aria-label="关闭扫码">×</button>
                </div>
                <div class="barcode-scan-body">
                    <div class="barcode-scan-video-wrap">
                        <video class="barcode-scan-video" autoplay muted playsinline></video>
                        <div class="barcode-scan-frame"></div>
                    </div>
                    <div class="barcode-scan-status">正在启动相机...</div>
                    <div class="barcode-scan-list-title">本次已扫</div>
                    <div class="barcode-scan-list"><div class="barcode-scan-empty">还没有扫到条码</div></div>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        overlay.querySelector('.barcode-scan-close').addEventListener('click', closeScanner);
        overlay.addEventListener('click', event => {
            if (event.target === overlay) closeScanner();
        });
        return overlay;
    }

    function setStatus(scanner, message, level) {
        scanner.status.textContent = message;
        scanner.status.className = `barcode-scan-status ${level || ''}`.trim();
    }

    function renderList(scanner) {
        if (!scanner.codes.length) {
            scanner.list.innerHTML = '<div class="barcode-scan-empty">还没有扫到条码</div>';
            return;
        }
        scanner.list.innerHTML = scanner.codes
            .map(code => `<div class="barcode-scan-code">${escapeHtml(code)}</div>`)
            .join('');
        scanner.list.scrollTop = scanner.list.scrollHeight;
    }

    function escapeHtml(text) {
        return String(text || '').replace(/[&<>"']/g, ch => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[ch]));
    }

    function stopStream(scanner) {
        if (!scanner || !scanner.stream) return;
        scanner.stream.getTracks().forEach(track => track.stop());
        scanner.stream = null;
    }

    function closeScanner() {
        if (!activeScanner) return;
        activeScanner.running = false;
        if (activeScanner.frameId) cancelAnimationFrame(activeScanner.frameId);
        stopStream(activeScanner);
        activeScanner.overlay.classList.remove('show');
        activeScanner.video.srcObject = null;
        activeScanner = null;
    }

    function notifyScan() {
        if (navigator.vibrate) navigator.vibrate(60);
    }

    function handleDetected(scanner, rawValue) {
        const code = normalizeBarcode(rawValue);
        if (!code) return;
        const now = Date.now();
        if (scanner.lastCode === code && now - scanner.lastCodeAt < 1500) return;
        scanner.lastCode = code;
        scanner.lastCodeAt = now;
        if (scanner.seen.has(code)) {
            setStatus(scanner, `已扫过 ${code}，继续扫描下一个。`, 'success');
            return;
        }
        scanner.seen.add(code);
        scanner.codes.push(code);
        renderList(scanner);
        notifyScan();
        try {
            const result = scanner.onCode ? scanner.onCode(code) : '';
            Promise.resolve(result).then(message => {
                setStatus(scanner, message || `已扫到 ${code}，继续扫描下一个。`, 'success');
            }).catch(error => {
                setStatus(scanner, error.message || '扫码处理失败', 'error');
            });
        } catch (error) {
            setStatus(scanner, error.message || '扫码处理失败', 'error');
        }
    }

    async function detectionLoop(scanner) {
        if (!scanner.running) return;
        if (scanner.video.readyState >= 2 && Date.now() - scanner.lastDetectAt >= 280) {
            scanner.lastDetectAt = Date.now();
            try {
                const results = await scanner.detector.detect(scanner.video);
                if (results && results.length) {
                    handleDetected(scanner, results[0].rawValue);
                }
            } catch (error) {
                if (scanner.running) setStatus(scanner, error.message || '识别失败，正在继续尝试。', 'error');
            }
        }
        scanner.frameId = requestAnimationFrame(() => detectionLoop(scanner));
    }

    async function openScanner(options) {
        closeScanner();
        const overlay = ensureOverlay();
        const scanner = {
            overlay,
            video: overlay.querySelector('.barcode-scan-video'),
            status: overlay.querySelector('.barcode-scan-status'),
            list: overlay.querySelector('.barcode-scan-list'),
            title: overlay.querySelector('.barcode-scan-title'),
            detector: null,
            stream: null,
            running: false,
            frameId: 0,
            lastDetectAt: 0,
            lastCode: '',
            lastCodeAt: 0,
            seen: new Set(),
            codes: [],
            onCode: options && options.onCode
        };
        activeScanner = scanner;
        scanner.title.textContent = (options && options.title) || '扫码输入条码';
        renderList(scanner);
        overlay.classList.add('show');

        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setStatus(scanner, '当前浏览器无法调用相机，请换手机浏览器或更新系统。', 'error');
            return;
        }
        if (!cameraAllowedByContext()) {
            setStatus(scanner, '浏览器要求 HTTPS 才能调用相机。云服务器请配置 HTTPS 后再用手机扫码。', 'error');
            return;
        }

        try {
            setStatus(scanner, '正在请求相机权限...');
            scanner.detector = await makeDetector();
            scanner.stream = await navigator.mediaDevices.getUserMedia({
                video: {
                    facingMode: { ideal: 'environment' },
                    width: { ideal: 1280 },
                    height: { ideal: 720 }
                },
                audio: false
            });
            scanner.video.srcObject = scanner.stream;
            scanner.video.setAttribute('playsinline', 'true');
            await scanner.video.play();
            scanner.running = true;
            setStatus(scanner, '对准条码即可连续扫描，扫完点右上角关闭。');
            detectionLoop(scanner);
        } catch (error) {
            stopStream(scanner);
            setStatus(scanner, error.message || '相机启动失败，请检查浏览器权限。', 'error');
        }
    }

    window.BarcodeCameraScanner = {
        open: openScanner,
        close: closeScanner,
        normalize: normalizeBarcode
    };
})();
