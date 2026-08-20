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

    // Touch tracking
    this.lastTouchPos = null;
    this.touchStartTime = 0;

    this.initEventListeners();
    this.initDragAndDrop();
    this.initVirtualKeyboard();
  }

  setEnabled(enabled) {
    this.enabled = enabled;
  }

  setViewOnly(viewOnly) {
    this.viewOnly = viewOnly;
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

    // Mouse Down / Up
    this.canvas.addEventListener('mousedown', (e) => this.onMouseDown(e));
    this.canvas.addEventListener('mouseup', (e) => this.onMouseUp(e));

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

        this.send({ type: 'keydown', key, code: key });
        setTimeout(() => {
          this.send({ type: 'keyup', key, code: key });
        }, 100);
      });
    });
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

    this.send({
      type: 'mousedown',
      button: e.button,
    });
  }

  onMouseUp(e) {
    if (!this.enabled || this.viewOnly) return;
    e.preventDefault();

    this.send({
      type: 'mouseup',
      button: e.button,
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

  onTouchStart(e) {
    if (!this.enabled || this.viewOnly || e.touches.length === 0) return;
    e.preventDefault();

    this.touchStartTime = performance.now();
    const touch = e.touches[0];
    const coords = this.renderer.normalizeCoordinates(touch.clientX, touch.clientY);
    this.lastTouchPos = coords;

    if (coords.inside) {
      this.send({
        type: 'mousemove',
        x: coords.x,
        y: coords.y,
      });
    }
  }

  onTouchMove(e) {
    if (!this.enabled || this.viewOnly || !this.lastTouchPos) return;
    e.preventDefault();

    if (e.touches.length === 1) {
      const touch = e.touches[0];
      const coords = this.renderer.normalizeCoordinates(touch.clientX, touch.clientY);
      if (coords.inside) {
        this.send({
          type: 'mousemove',
          x: coords.x,
          y: coords.y,
        });
      }
    } else if (e.touches.length === 2) {
      const touch = e.touches[0];
      const coords = this.renderer.normalizeCoordinates(touch.clientX, touch.clientY);
      const dy = (coords.y - this.lastTouchPos.y) * 800;
      this.send({
        type: 'wheel',
        dx: 0,
        dy: -dy,
      });
      this.lastTouchPos = coords;
    }
  }

  onTouchEnd(e) {
    if (!this.enabled || this.viewOnly) return;
    e.preventDefault();

    const duration = performance.now() - this.touchStartTime;
    if (duration < 250 && this.lastTouchPos && this.lastTouchPos.inside) {
      this.send({
        type: 'click',
        button: 0,
        count: 1,
      });
    }
    this.lastTouchPos = null;
  }
}
