/**
 * CanvasRenderer - Hardware-accelerated HTML5 Canvas video renderer.
 * Supports offscreen backing-canvas dirty-rectangle compositing (RDP2 protocol),
 * high-DPI crisp Lanczos/bicubic scaling, pinch-to-zoom & pan (1.0x - 4.0x),
 * requestAnimationFrame VSync coalescing, remote cursor overlay, and coordinate normalization.
 */

class CanvasRenderer {
  constructor(canvasElement) {
    this.canvas = canvasElement;
    this.ctx = this.canvas.getContext('2d', { alpha: false, desynchronized: true });

    // Persistent offscreen backing buffer for zero-flicker dirty-rect delta patches
    this.backingCanvas = document.createElement('canvas');
    this.backingCanvas.width = 1920;
    this.backingCanvas.height = 1080;
    this.backingCtx = this.backingCanvas.getContext('2d', { alpha: false });
    this._hasBackingFrame = false;
    this._rafPending = false;

    this.remoteWidth = 1920;
    this.remoteHeight = 1080;
    this.scalingMode = 'fit'; // 'fit' | 'fill' | 'original'
    this.showRemoteCursor = true;
    this.remoteCursor = { x: 0.5, y: 0.5 };

    // Interactive Zoom & Pan state (1.0x - 4.0x for mobile & precision HiDPI inspection)
    this.zoom = 1.0;
    this.panX = 0;
    this.panY = 0;

    // Viewport layout state
    this.layout = {
      offsetX: 0,
      offsetY: 0,
      drawWidth: 1920,
      drawHeight: 1080,
      scaleX: 1.0,
      scaleY: 1.0,
    };

    window.addEventListener('resize', () => this.resizeCanvas());
    this.resizeCanvas();
  }

  resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const winWidth = window.innerWidth;
    const winHeight = window.innerHeight;

    this.canvas.width = Math.max(1, Math.round(winWidth * dpr));
    this.canvas.height = Math.max(1, Math.round(winHeight * dpr));
    this.canvas.style.width = `${winWidth}px`;
    this.canvas.style.height = `${winHeight}px`;

    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.ctx.imageSmoothingEnabled = true;
    if ('imageSmoothingQuality' in this.ctx) {
      this.ctx.imageSmoothingQuality = 'high';
    }

    this.updateLayout();
    if (this._hasBackingFrame) {
      this.scheduleRender();
    }
  }

  _computeBaseLayout(winWidth, winHeight) {
    if (this.scalingMode === 'fill') {
      return {
        baseOffsetX: 0,
        baseOffsetY: 0,
        baseDrawW: winWidth,
        baseDrawH: winHeight,
      };
    }

    if (this.scalingMode === 'original') {
      return {
        baseOffsetX: Math.max(0, (winWidth - this.remoteWidth) / 2),
        baseOffsetY: Math.max(0, (winHeight - this.remoteHeight) / 2),
        baseDrawW: this.remoteWidth,
        baseDrawH: this.remoteHeight,
      };
    }

    // Default: Aspect-ratio preserving 'fit' (letterboxing)
    const remoteAspect = this.remoteWidth / Math.max(1, this.remoteHeight);
    const windowAspect = winWidth / Math.max(1, winHeight);

    let baseDrawW, baseDrawH;
    if (windowAspect > remoteAspect) {
      baseDrawH = winHeight;
      baseDrawW = winHeight * remoteAspect;
    } else {
      baseDrawW = winWidth;
      baseDrawH = winWidth / remoteAspect;
    }

    return {
      baseOffsetX: (winWidth - baseDrawW) / 2,
      baseOffsetY: (winHeight - baseDrawH) / 2,
      baseDrawW,
      baseDrawH,
    };
  }

  updateLayout() {
    const winWidth = window.innerWidth;
    const winHeight = window.innerHeight;
    const { baseOffsetX, baseOffsetY, baseDrawW, baseDrawH } = this._computeBaseLayout(winWidth, winHeight);

    if (this.zoom <= 1.001) {
      this.zoom = 1.0;
      this.panX = 0;
      this.panY = 0;
    }

    const drawW = baseDrawW * this.zoom;
    const drawH = baseDrawH * this.zoom;

    const maxPanX = Math.max(0, (drawW - winWidth) / 2);
    const maxPanY = Math.max(0, (drawH - winHeight) / 2);
    this.panX = Math.max(-maxPanX, Math.min(maxPanX, this.panX));
    this.panY = Math.max(-maxPanY, Math.min(maxPanY, this.panY));

    const centerOffsetX = baseOffsetX - (drawW - baseDrawW) / 2;
    const centerOffsetY = baseOffsetY - (drawH - baseDrawH) / 2;

    this.layout.drawWidth = drawW;
    this.layout.drawHeight = drawH;
    this.layout.offsetX = centerOffsetX + this.panX;
    this.layout.offsetY = centerOffsetY + this.panY;
    this.layout.scaleX = drawW / Math.max(1, this.remoteWidth);
    this.layout.scaleY = drawH / Math.max(1, this.remoteHeight);
  }

  setZoom(newZoom, focalClientX, focalClientY) {
    const clampedZoom = Math.max(1.0, Math.min(4.0, newZoom));
    if (Math.abs(clampedZoom - this.zoom) < 0.001) return this.zoom;

    const rect = this.canvas.getBoundingClientRect();
    const canvasX = (typeof focalClientX === 'number' ? focalClientX : window.innerWidth / 2) - rect.left;
    const canvasY = (typeof focalClientY === 'number' ? focalClientY : window.innerHeight / 2) - rect.top;

    const normX = (canvasX - this.layout.offsetX) / Math.max(1, this.layout.drawWidth);
    const normY = (canvasY - this.layout.offsetY) / Math.max(1, this.layout.drawHeight);

    this.zoom = clampedZoom;

    const winWidth = window.innerWidth;
    const winHeight = window.innerHeight;
    const { baseOffsetX, baseOffsetY, baseDrawW, baseDrawH } = this._computeBaseLayout(winWidth, winHeight);
    const newDrawW = baseDrawW * this.zoom;
    const newDrawH = baseDrawH * this.zoom;
    const newCenterOffsetX = baseOffsetX - (newDrawW - baseDrawW) / 2;
    const newCenterOffsetY = baseOffsetY - (newDrawH - baseDrawH) / 2;

    this.panX = (canvasX - normX * newDrawW) - newCenterOffsetX;
    this.panY = (canvasY - normY * newDrawH) - newCenterOffsetY;

    this.updateLayout();
    if (this._hasBackingFrame) {
      this.scheduleRender();
    }
    return this.zoom;
  }

  panBy(dx, dy) {
    if (this.zoom <= 1.001) return;
    this.panX += dx;
    this.panY += dy;
    this.updateLayout();
    if (this._hasBackingFrame) {
      this.scheduleRender();
    }
  }

  resetZoom() {
    this.zoom = 1.0;
    this.panX = 0;
    this.panY = 0;
    this.updateLayout();
    if (this._hasBackingFrame) {
      this.scheduleRender();
    }
  }

  setRemoteResolution(width, height) {
    if (width > 0 && height > 0 && (this.remoteWidth !== width || this.remoteHeight !== height)) {
      this.remoteWidth = width;
      this.remoteHeight = height;
      this.updateLayout();
    }
  }

  setRemoteCursor(normX, normY) {
    if (typeof normX === 'number' && typeof normY === 'number') {
      this.remoteCursor.x = normX;
      this.remoteCursor.y = normY;
    }
  }

  setScalingMode(mode) {
    this.scalingMode = mode;
    this.updateLayout();
    if (this._hasBackingFrame) {
      this.scheduleRender();
    }
  }

  /**
   * Apply a full keyframe or dirty-rectangle delta patch onto the persistent backing canvas
   * and coalesce viewport presentation via requestAnimationFrame.
   */
  applyFramePatch(imageSource, fullW, fullH, rectX, rectY, rectW, rectH, isFullFrame, origW, origH) {
    const nativeW = origW > 0 ? origW : fullW;
    const nativeH = origH > 0 ? origH : fullH;
    this.setRemoteResolution(nativeW, nativeH);

    if (this.backingCanvas.width !== fullW || this.backingCanvas.height !== fullH) {
      this.backingCanvas.width = fullW;
      this.backingCanvas.height = fullH;
      this.backingCtx.imageSmoothingEnabled = false;
    }

    if (isFullFrame) {
      this.backingCtx.drawImage(imageSource, 0, 0, fullW, fullH);
    } else {
      this.backingCtx.drawImage(
        imageSource,
        0, 0, imageSource.width, imageSource.height,
        rectX, rectY, rectW, rectH
      );
    }

    this._hasBackingFrame = true;
    this.scheduleRender();
  }

  /**
   * Coalesce screen repaints onto VSync via requestAnimationFrame.
   */
  scheduleRender() {
    if (this._rafPending) return;
    this._rafPending = true;
    requestAnimationFrame(() => {
      this._rafPending = false;
      this.renderNow();
    });
  }

  /**
   * Paint the persistent backing desktop buffer and remote cursor indicator onto the visible canvas.
   */
  renderNow() {
    if (!this._hasBackingFrame) return;

    const winWidth = window.innerWidth;
    const winHeight = window.innerHeight;

    this.ctx.fillStyle = '#000000';
    this.ctx.fillRect(0, 0, winWidth, winHeight);

    this.ctx.imageSmoothingEnabled = true;
    if ('imageSmoothingQuality' in this.ctx) {
      this.ctx.imageSmoothingQuality = 'high';
    }

    this.ctx.drawImage(
      this.backingCanvas,
      0, 0, this.backingCanvas.width, this.backingCanvas.height,
      this.layout.offsetX, this.layout.offsetY,
      this.layout.drawWidth, this.layout.drawHeight
    );

    if (this.showRemoteCursor && this.remoteCursor) {
      const cx = this.layout.offsetX + this.remoteCursor.x * this.layout.drawWidth;
      const cy = this.layout.offsetY + this.remoteCursor.y * this.layout.drawHeight;

      this.ctx.save();
      this.ctx.beginPath();
      this.ctx.arc(cx, cy, 5, 0, Math.PI * 2);
      this.ctx.fillStyle = 'rgba(0, 212, 255, 0.85)';
      this.ctx.fill();
      this.ctx.lineWidth = 1.5;
      this.ctx.strokeStyle = '#ffffff';
      this.ctx.stroke();
      this.ctx.restore();
    }
  }

  /**
   * Legacy full-frame render helper (routes through backingCanvas for consistent zoom/pan/resize).
   */
  renderFrame(imageSource) {
    const w = imageSource.width || this.remoteWidth;
    const h = imageSource.height || this.remoteHeight;
    if (this.backingCanvas.width !== w || this.backingCanvas.height !== h) {
      this.backingCanvas.width = w;
      this.backingCanvas.height = h;
    }
    this.backingCtx.drawImage(imageSource, 0, 0, w, h);
    this._hasBackingFrame = true;
    this.renderNow();
  }

  /**
   * Translate viewport click/mouse/touch coordinates into normalized 0.0 - 1.0 host desktop space.
   */
  normalizeCoordinates(clientX, clientY) {
    const rect = this.canvas.getBoundingClientRect();
    const canvasX = clientX - rect.left;
    const canvasY = clientY - rect.top;

    const imageX = canvasX - this.layout.offsetX;
    const imageY = canvasY - this.layout.offsetY;

    const inside = (
      imageX >= 0 &&
      imageX <= this.layout.drawWidth &&
      imageY >= 0 &&
      imageY <= this.layout.drawHeight
    );

    const normX = Math.max(0.0, Math.min(1.0, imageX / Math.max(1, this.layout.drawWidth)));
    const normY = Math.max(0.0, Math.min(1.0, imageY / Math.max(1, this.layout.drawHeight)));

    return { x: normX, y: normY, inside };
  }
}
