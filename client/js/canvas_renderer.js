/**
 * CanvasRenderer - Hardware-accelerated HTML5 Canvas video renderer.
 * Handles high-DPI scaling, letterboxing, remote cursor overlay, and coordinate space normalization.
 */

class CanvasRenderer {
  constructor(canvasElement) {
    this.canvas = canvasElement;
    this.ctx = this.canvas.getContext('2d', { alpha: false, desynchronized: true });

    this.remoteWidth = 1920;
    this.remoteHeight = 1080;
    this.scalingMode = 'fit'; // 'fit' | 'fill' | 'original'
    this.showRemoteCursor = true;
    this.remoteCursor = { x: 0.5, y: 0.5 };

    // Letterbox layout calculations
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

    this.canvas.width = winWidth * dpr;
    this.canvas.height = winHeight * dpr;
    this.canvas.style.width = `${winWidth}px`;
    this.canvas.style.height = `${winHeight}px`;

    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.updateLayout();
  }

  updateLayout() {
    const winWidth = window.innerWidth;
    const winHeight = window.innerHeight;

    if (this.scalingMode === 'fill') {
      this.layout.offsetX = 0;
      this.layout.offsetY = 0;
      this.layout.drawWidth = winWidth;
      this.layout.drawHeight = winHeight;
      this.layout.scaleX = winWidth / this.remoteWidth;
      this.layout.scaleY = winHeight / this.remoteHeight;
      return;
    }

    if (this.scalingMode === 'original') {
      this.layout.drawWidth = this.remoteWidth;
      this.layout.drawHeight = this.remoteHeight;
      this.layout.offsetX = Math.max(0, (winWidth - this.remoteWidth) / 2);
      this.layout.offsetY = Math.max(0, (winHeight - this.remoteHeight) / 2);
      this.layout.scaleX = 1.0;
      this.layout.scaleY = 1.0;
      return;
    }

    // Default: Aspect-ratio preserving 'fit' (letterboxing)
    const remoteAspect = this.remoteWidth / this.remoteHeight;
    const windowAspect = winWidth / winHeight;

    let drawW, drawH;
    if (windowAspect > remoteAspect) {
      drawH = winHeight;
      drawW = winHeight * remoteAspect;
    } else {
      drawW = winWidth;
      drawH = winWidth / remoteAspect;
    }

    this.layout.drawWidth = drawW;
    this.layout.drawHeight = drawH;
    this.layout.offsetX = (winWidth - drawW) / 2;
    this.layout.offsetY = (winHeight - drawH) / 2;
    this.layout.scaleX = drawW / this.remoteWidth;
    this.layout.scaleY = drawH / this.remoteHeight;
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
  }

  /**
   * Render an ImageBitmap or Image element onto the canvas, plus optional remote cursor indicator.
   */
  renderFrame(imageSource) {
    const winWidth = window.innerWidth;
    const winHeight = window.innerHeight;

    // Clear background black bars
    this.ctx.fillStyle = '#000000';
    this.ctx.fillRect(0, 0, winWidth, winHeight);

    // Draw frame within layout bounds
    this.ctx.drawImage(
      imageSource,
      0, 0, imageSource.width, imageSource.height,
      this.layout.offsetX, this.layout.offsetY,
      this.layout.drawWidth, this.layout.drawHeight
    );

    // Render RustDesk-style remote cursor indicator if enabled
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
   * Translate viewport click/mouse coordinates into normalized 0.0 - 1.0 host desktop space.
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

    const normX = Math.max(0.0, Math.min(1.0, imageX / this.layout.drawWidth));
    const normY = Math.max(0.0, Math.min(1.0, imageY / this.layout.drawHeight));

    return { x: normX, y: normY, inside };
  }
}
