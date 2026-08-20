/**
 * CanvasRenderer - Hardware-accelerated HTML5 Canvas video renderer.
 * Handles high-DPI scaling, letterboxing, and coordinate space normalization.
 */

class CanvasRenderer {
  constructor(canvasElement) {
    this.canvas = canvasElement;
    this.ctx = this.canvas.getContext('2d', { alpha: false, desynchronized: true });

    this.remoteWidth = 1920;
    this.remoteHeight = 1080;
    this.scalingMode = 'fit'; // 'fit' | 'fill' | 'original'

    // Letterbox layout calculations
    this.layout = {
      offsetX: 0,
      offsetY: 0,
      drawWidth: 1920,
      drawHeight: 1080,
      scaleX: 1.0,
      scaleY: 1.0,
    };

    // Listen to window resize
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

    this.ctx.scale(dpr, dpr);
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
      // Window is wider than remote aspect: letterbox left/right
      drawH = winHeight;
      drawW = winHeight * remoteAspect;
    } else {
      // Window is taller than remote aspect: letterbox top/bottom
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

  setScalingMode(mode) {
    this.scalingMode = mode;
    this.updateLayout();
  }

  /**
   * Render an ImageBitmap or Image element onto the canvas.
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
  }

  /**
   * Translate viewport click/mouse coordinates into normalized 0.0 - 1.0 host desktop space.
   */
  normalizeCoordinates(clientX, clientY) {
    const rect = this.canvas.getBoundingClientRect();
    const canvasX = clientX - rect.left;
    const canvasY = clientY - rect.top;

    // Relative to the drawn image rectangle
    const imageX = canvasX - this.layout.offsetX;
    const imageY = canvasY - this.layout.offsetY;

    // Check if inside bounds
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
