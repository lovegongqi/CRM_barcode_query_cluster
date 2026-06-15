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

    function hasNativeDetector() {
        return 'BarcodeDetector' in window;
    }

    function hasZxingFallback() {
        return !!(window.ZXing && window.ZXing.BrowserMultiFormatReader);
    }

    async function makeNativeDetector() {
        if (typeof BarcodeDetector.getSupportedFormats !== 'function') {
            return new BarcodeDetector();
        }
        const supported = await BarcodeDetector.getSupportedFormats();
        const formats = preferredFormats.filter(format => supported.includes(format));
        return formats.length ? new BarcodeDetector({ formats }) : new BarcodeDetector();
    }

    function isCameraPermissionError(error) {
        return error && ['NotAllowedError', 'PermissionDeniedError', 'SecurityError'].includes(error.name);
    }

    function isZxingNotFound(error) {
        if (!error) return true;
        const name = error.name || (error.constructor && error.constructor.name) || '';
        return name === 'NotFoundException' || name === 'ChecksumException' || name === 'FormatException';
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
        if (activeScanner.zxingReader) activeScanner.zxingReader.reset();
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

    async function startNativeScanner(scanner) {
        scanner.detector = await makeNativeDetector();
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
    }

    async function startZxingScanner(scanner) {
        if (!hasZxingFallback()) {
            throw new Error('当前浏览器不支持原生扫码，兼容扫码库也未加载，请刷新页面后重试。');
        }
        const reader = new window.ZXing.BrowserMultiFormatReader(undefined, 350);
        scanner.zxingReader = reader;
        scanner.running = true;
        scanner.video.setAttribute('playsinline', 'true');
        setStatus(scanner, '当前浏览器不支持原生扫码，已切换兼容模式。正在请求相机权限...');
        await reader.decodeFromConstraints({
            video: {
                facingMode: { ideal: 'environment' },
                width: { ideal: 1280 },
                height: { ideal: 720 }
            },
            audio: false
        }, scanner.video, (result, error) => {
            if (!scanner.running) return;
            if (result) {
                const text = typeof result.getText === 'function' ? result.getText() : result.text;
                handleDetected(scanner, text);
                return;
            }
            if (error && !isZxingNotFound(error)) {
                const now = Date.now();
                if (!scanner.lastErrorAt || now - scanner.lastErrorAt > 2500) {
                    scanner.lastErrorAt = now;
                    setStatus(scanner, error.message || '兼容扫码识别失败，正在继续尝试。', 'error');
                }
            }
        });
        setStatus(scanner, '兼容扫码已启动。对准条码即可连续扫描，扫完点右上角关闭。');
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
            zxingReader: null,
            stream: null,
            running: false,
            frameId: 0,
            lastDetectAt: 0,
            lastErrorAt: 0,
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
            if (hasNativeDetector()) {
                setStatus(scanner, '正在请求相机权限...');
                await startNativeScanner(scanner);
            } else {
                await startZxingScanner(scanner);
            }
        } catch (error) {
            stopStream(scanner);
            if (hasNativeDetector() && hasZxingFallback() && !isCameraPermissionError(error)) {
                try {
                    await startZxingScanner(scanner);
                    return;
                } catch (fallbackError) {
                    setStatus(scanner, fallbackError.message || '兼容扫码启动失败，请检查浏览器权限。', 'error');
                    return;
                }
            }
            setStatus(scanner, error.message || '相机启动失败，请检查浏览器权限。', 'error');
        }
    }

    window.BarcodeCameraScanner = {
        open: openScanner,
        close: closeScanner,
        normalize: normalizeBarcode
    };
})();
