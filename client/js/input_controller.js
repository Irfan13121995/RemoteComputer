/**
 * InputController - Captures, normalizes, and transmits mouse, keyboard, and touch events.
 * Supports Pointer Lock, Canvas Drag-and-Drop file uploads, and On-Screen Virtual Keyboard.
 */

class InputController {
  constructor(canvasElement, canvasRenderer, sendCallback) {
    this.canvas = canvasElement;
    this.renderer = canvasRenderer;
    this.send = sendCallback;

    this.isPointerLocked = false;
    this.mapCmdToCtrl = true;
    this.enabled = false;
    this.viewOnly = false;

    // Movement throttling
    this.lastMouseMoveTime = 0;
    this.mouseMoveThrottleMs = 12; // ~80Hz max update rate

    // Touch & Mobile Trackpad tracking
    this.touchMode = localStorage.getItem('rd_touch_mode') || 'touch'; // 'touch' | 'trackpad'
    this.lastTouchPos = null;
    this.lastRawTouch = null;
    this.touchStartTime = 0;
    this.lastTapTime = 0;
    this.touchMovedDistance = 0;
    this.maxTouchesInGesture = 0;
    this.longPressTimer = null;
    this.longPressTriggered = false;

    this.initEventListeners();
    this.initDragAndDrop();
    this.initVirtualKeyboard();
    this.initMobileSoftKeyboard();
  }

  setEnabled(enabled) {
    this.enabled = enabled;
  }

  setViewOnly(viewOnly) {
    this.viewOnly = viewOnly;
  }

  setTouchMode(mode) {
    this.touchMode = mode === 'trackpad' ? 'trackpad' : 'touch';
    localStorage.setItem('rd_touch_mode', this.touchMode);
    this.vibrate(15);
    return this.touchMode;
  }

  toggleTouchMode() {
    return this.setTouchMode(this.touchMode === 'touch' ? 'trackpad' : 'touch');
  }

  vibrate(ms = 25) {
    try {
      if (window.AndroidBridge && typeof window.AndroidBridge.vibrate === 'function') {
        window.AndroidBridge.vibrate(ms);
      } else if (navigator.vibrate) {
        navigator.vibrate(ms);
      }
    } catch (e) {}
  }

  initEventListeners() {
    // Pointer Lock Change
    document.addEventListener('pointerlockchange', () => {
      this.isPointerLocked = (document.pointerLockElement === this.canvas);
      if (this.isPointerLocked) {
        this.canvas.classList.add('pointer-locked');
      } else {
        this.canvas.classList.remove('pointer-locked');
      }
    });

    // Mouse Movement
    this.canvas.addEventListener('mousemove', (e) => this.onMouseMove(e));

    // Mouse Down / Up / Double-Click
    this.canvas.addEventListener('mousedown', (e) => this.onMouseDown(e));
    this.canvas.addEventListener('mouseup', (e) => this.onMouseUp(e));
    this.canvas.addEventListener('dblclick', (e) => this.onDblClick(e));

    // Wheel
    this.canvas.addEventListener('wheel', (e) => this.onWheel(e), { passive: false });

    // Prevent default context menu on remote desktop
    this.canvas.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      return false;
    });

    // Keyboard Events on Window
    window.addEventListener('keydown', (e) => this.onKeyDown(e));
    window.addEventListener('keyup', (e) => this.onKeyUp(e));

    // Touch Events on Canvas
    this.canvas.addEventListener('touchstart', (e) => this.onTouchStart(e), { passive: false });
    this.canvas.addEventListener('touchmove', (e) => this.onTouchMove(e), { passive: false });
    this.canvas.addEventListener('touchend', (e) => this.onTouchEnd(e), { passive: false });
    this.canvas.addEventListener('touchcancel', () => this.clearLongPress());
  }

  initDragAndDrop() {
    const dropOverlay = document.getElementById('drop-overlay');
    if (!dropOverlay) return;

    window.addEventListener('dragover', (e) => {
      e.preventDefault();
      if (this.enabled) {
        dropOverlay.classList.remove('hidden');
      }
    });

    dropOverlay.addEventListener('dragleave', (e) => {
      e.preventDefault();
      dropOverlay.classList.add('hidden');
    });

    dropOverlay.addEventListener('drop', async (e) => {
      e.preventDefault();
      dropOverlay.classList.add('hidden');

      if (!this.enabled || !e.dataTransfer.files || e.dataTransfer.files.length === 0) return;

      const files = Array.from(e.dataTransfer.files);
      if (window.app && window.app.uploadDroppedFiles) {
        window.app.uploadDroppedFiles(files);
      }
    });
  }

  initVirtualKeyboard() {
    document.querySelectorAll('.vk-key').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const key = e.currentTarget.getAttribute('data-key');
        if (!key || !this.enabled) return;

        this.vibrate(15);
        this.send({ type: 'keydown', key, code: key });
        setTimeout(() => {
          this.send({ type: 'keyup', key, code: key });
        }, 90);
      });
    });
  }

  initMobileSoftKeyboard() {
    const imeInput = document.getElementById('mobile-ime-input');
    if (!imeInput) return;

    imeInput.addEventListener('input', (e) => {
      if (!this.enabled || this.viewOnly) {
        imeInput.value = '';
        return;
      }
      const val = imeInput.value;
      if (val && val.length > 0) {
        for (const ch of val) {
          this.send({ type: 'keydown', key: ch, code: '' });
          this.send({ type: 'keyup', key: ch, code: '' });
        }
      }
      imeInput.value = '';
    });

    imeInput.addEventListener('keydown', (e) => {
      if (!this.enabled || this.viewOnly) return;
      if (['Backspace', 'Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
        e.preventDefault();
        this.send({ type: 'keydown', key: e.key, code: e.code || e.key });
        setTimeout(() => {
          this.send({ type: 'keyup', key: e.key, code: e.code || e.key });
        }, 60);
      }
    });
  }

  openMobileSoftKeyboard() {
    const imeInput = document.getElementById('mobile-ime-input');
    if (imeInput) {
      imeInput.value = '';
      imeInput.focus();
      imeInput.click();
    }
    if (window.AndroidBridge && typeof window.AndroidBridge.showSoftKeyboard === 'function') {
      window.AndroidBridge.showSoftKeyboard();
    }
  }

  togglePointerLock() {
    if (document.pointerLockElement === this.canvas) {
      document.exitPointerLock();
    } else {
      this.canvas.requestPointerLock().catch((err) => {
        console.warn('Pointer lock failed:', err);
      });
    }
  }

  onMouseMove(e) {
    if (!this.enabled || this.viewOnly) return;

    if (this.isPointerLocked) {
      this.send({
        type: 'mousemove_rel',
        dx: e.movementX,
        dy: e.movementY,
      });
      return;
    }

    const now = performance.now();
    if (now - this.lastMouseMoveTime < this.mouseMoveThrottleMs) {
      return;
    }
    this.lastMouseMoveTime = now;

    const coords = this.renderer.normalizeCoordinates(e.clientX, e.clientY);
    if (coords.inside) {
      this.send({
        type: 'mousemove',
        x: coords.x,
        y: coords.y,
      });
    }
  }

  onMouseDown(e) {
    if (!this.enabled || this.viewOnly) return;
    e.preventDefault();
    try {
      this.canvas.focus({ preventScroll: true });
    } catch (err) {}

    if (this.isPointerLocked) {
      this.send({ type: 'mousedown', button: e.button });
      return;
    }

    const coords = this.renderer.normalizeCoordinates(e.clientX, e.clientY);
    this.send({
      type: 'mousedown',
      button: e.button,
      x: coords.x,
      y: coords.y,
    });
  }

  onMouseUp(e) {
    if (!this.enabled || this.viewOnly) return;
    e.preventDefault();

    if (this.isPointerLocked) {
      this.send({ type: 'mouseup', button: e.button });
      return;
    }

    const coords = this.renderer.normalizeCoordinates(e.clientX, e.clientY);
    this.send({
      type: 'mouseup',
      button: e.button,
      x: coords.x,
      y: coords.y,
    });
  }

  onDblClick(e) {
    if (!this.enabled || this.viewOnly) return;
    e.preventDefault();

    const coords = this.renderer.normalizeCoordinates(e.clientX, e.clientY);
    this.send({
      type: 'click',
      button: 0,
      count: 2,
      x: coords.x,
      y: coords.y,
    });
  }

  onWheel(e) {
    if (!this.enabled || this.viewOnly) return;
    e.preventDefault();

    this.send({
      type: 'wheel',
      dx: e.deltaX,
      dy: e.deltaY,
    });
  }

  onKeyDown(e) {
    if (!this.enabled || this.viewOnly) return;

    // Ignore if typing in an input element or modal dialog
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
      return;
    }

    // Suppress conflicting browser defaults
    const interceptedKeys = ['Tab', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12', 'Escape'];
    if (interceptedKeys.includes(e.key) || e.ctrlKey || e.metaKey || e.altKey) {
      if (e.key !== 'F12' && e.key !== 'I') {
        e.preventDefault();
      }
    }

    let keyToSend = e.key;
    if (this.mapCmdToCtrl && (e.key === 'Meta' || e.key === 'OS')) {
      keyToSend = 'Control';
    }

    this.send({
      type: 'keydown',
      key: keyToSend,
      code: e.code,
    });
  }

  onKeyUp(e) {
    if (!this.enabled || this.viewOnly) return;

    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
      return;
    }

    let keyToSend = e.key;
    if (this.mapCmdToCtrl && (e.key === 'Meta' || e.key === 'OS')) {
      keyToSend = 'Control';
    }

    this.send({
      type: 'keyup',
      key: keyToSend,
      code: e.code,
    });
  }

  clearLongPress() {
    if (this.longPressTimer) {
      clearTimeout(this.longPressTimer);
      this.longPressTimer = null;
    }
  }

  onTouchStart(e) {
    if (!this.enabled || this.viewOnly || e.touches.length === 0) return;
    e.preventDefault();

    this.clearLongPress();
    this.touchStartTime = performance.now();
    this.touchMovedDistance = 0;
    this.longPressTriggered = false;
    this.maxTouchesInGesture = Math.max(this.maxTouchesInGesture, e.touches.length);

    const touch = e.touches[0];
    this.lastRawTouch = { x: touch.clientX, y: touch.clientY };
    const coords = this.renderer.normalizeCoordinates(touch.clientX, touch.clientY);
    this.lastTouchPos = coords;

    if (e.touches.length === 1) {
      if (this.touchMode === 'touch' && coords.inside) {
        this.send({
          type: 'mousemove',
          x: coords.x,
          y: coords.y,
        });
      }

      // Schedule Long-Press (480ms) for Right-Click with Haptic Vibration
      this.longPressTimer = setTimeout(() => {
        if (this.touchMovedDistance < 14 && this.maxTouchesInGesture === 1) {
          this.longPressTriggered = true;
          this.vibrate(45);
          this.send({
            type: 'click',
            button: 2,
            count: 1,
          });
        }
      }, 480);
    }
  }

  onTouchMove(e) {
    if (!this.enabled || this.viewOnly || !this.lastRawTouch) return;
    e.preventDefault();

    const touch = e.touches[0];
    const rawDx = touch.clientX - this.lastRawTouch.x;
    const rawDy = touch.clientY - this.lastRawTouch.y;
    this.touchMovedDistance += Math.hypot(rawDx, rawDy);

    if (this.touchMovedDistance > 14) {
      this.clearLongPress();
    }

    if (e.touches.length === 1) {
      if (this.touchMode === 'trackpad') {
        // Relative cursor movement like RustDesk Mobile Trackpad mode
        const sensitivity = 1.65;
        this.send({
          type: 'mousemove_rel',
          dx: Math.round(rawDx * sensitivity),
          dy: Math.round(rawDy * sensitivity),
        });
      } else {
        const coords = this.renderer.normalizeCoordinates(touch.clientX, touch.clientY);
        if (coords.inside) {
          this.send({
            type: 'mousemove',
            x: coords.x,
            y: coords.y,
          });
        }
        this.lastTouchPos = coords;
      }
    } else if (e.touches.length === 2) {
      // Two-finger vertical scroll
      this.clearLongPress();
      const scrollDy = rawDy * 3.5;
      if (Math.abs(scrollDy) > 1) {
        this.send({
          type: 'wheel',
          dx: 0,
          dy: -Math.round(scrollDy),
        });
      }
    }

    this.lastRawTouch = { x: touch.clientX, y: touch.clientY };
  }

  onTouchEnd(e) {
    if (!this.enabled || this.viewOnly) return;
    e.preventDefault();

    this.clearLongPress();
    const duration = performance.now() - this.touchStartTime;

    if (!this.longPressTriggered) {
      if (this.maxTouchesInGesture === 2 && duration < 300 && this.touchMovedDistance < 24) {
        // Two-finger tap -> Right Click
        this.vibrate(30);
        this.send({
          type: 'click',
          button: 2,
          count: 1,
        });
      } else if (this.maxTouchesInGesture === 1 && duration < 280 && this.touchMovedDistance < 16) {
        // Single-finger tap or double-tap -> Left Click / Double Click
        const now = performance.now();
        const isDoubleTap = (now - this.lastTapTime) < 320;
        this.lastTapTime = isDoubleTap ? 0 : now;
        this.vibrate(isDoubleTap ? 25 : 15);
        const payload = {
          type: 'click',
          button: 0,
          count: isDoubleTap ? 2 : 1,
        };
        if (this.touchMode === 'touch' && this.lastTouchPos && this.lastTouchPos.inside) {
          payload.x = this.lastTouchPos.x;
          payload.y = this.lastTouchPos.y;
        }
        this.send(payload);
      }
    }

    if (e.touches.length === 0) {
      this.lastTouchPos = null;
      this.lastRawTouch = null;
      this.maxTouchesInGesture = 0;
    }
  }
}

