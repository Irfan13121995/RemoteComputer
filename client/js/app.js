/**
 * RemoteDesk Pro v2.0 - Comprehensive RustDesk-Inspired Web & Desktop Client Suite
 * Orchestrates Remote Desktop, LAN/Rendezvous Discovery, Audio Streaming, Privacy Mode,
 * TCP Port Tunneling, File Transfer + Folder ZIP, Terminal, Task Manager, Security Policy, and Address Book.
 */

class RemoteDeskApp {
  constructor() {
    this.ws = null;
    this.termWs = null;
    this.token = localStorage.getItem('rd_session_token') || '';
    this.activeHostUrl = window.location.origin;
    this.connected = false;
    this.lastFrameMeta = null;

    // Performance metrics
    this.fpsCounter = 0;
    this.currentFps = 0;
    this.currentLatency = 0;
    this.bandwidthBytes = 0;
    this.currentBandwidthKbps = 0;

    // Session Feature States
    this.audioMuted = true;
    this.audioCtx = null;
    this.privacyModeEnabled = false;

    // Video Recording State
    this.mediaRecorder = null;
    this.recordedChunks = [];
    this.isRecording = false;

    // File Manager State
    this.currentFmPath = '';
    this.parentFmPath = '';
    this.desktopUploadPath = '';

    // Task Manager Polling Interval
    this.diagInterval = null;

    // DOM Elements
    this.hubView = document.getElementById('hub-view');
    this.sessionView = document.getElementById('session-view');
    this.canvas = document.getElementById('remote-canvas');

    this.renderer = new CanvasRenderer(this.canvas);
    this.input = new InputController(this.canvas, this.renderer, (msg) => this.sendWs(msg));

    this.initTheme();
    this.initDomElements();
    this.bindEvents();
    this.bindSuiteTabs();
    this.startMetricsTimer();
    this.fetchHostInfo();
    this.fetchDiscoveredPeers();
    this.loadAddressBook();

    // Poll LAN Discovery peers every 12s while on Hub
    setInterval(() => {
      if (!this.connected) this.fetchDiscoveredPeers();
    }, 12000);
  }

  // ── THEME MANAGEMENT ────────────────────────────────────────────────────────
  initTheme() {
    const savedTheme = localStorage.getItem('rd_theme') || 'dark';
    document.documentElement.setAttribute('data-theme', savedTheme);
    const themeIcon = document.getElementById('theme-icon');
    if (themeIcon) {
      themeIcon.innerText = savedTheme === 'dark' ? '🌙' : '☀️';
    }
  }

  toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('rd_theme', next);
    const themeIcon = document.getElementById('theme-icon');
    if (themeIcon) {
      themeIcon.innerText = next === 'dark' ? '🌙' : '☀️';
    }
    this.showToast(`Switched to ${next} theme`, 'info');
  }

  // ── DOM ELEMENTS & EVENT BINDINGS ───────────────────────────────────────────
  initDomElements() {
    // Hub
    this.hostMachineId = document.getElementById('host-machine-id');
    this.hostPinDisplay = document.getElementById('host-pin-display');
    this.hostDisplayInfo = document.getElementById('host-display-info');
    this.hubIpList = document.getElementById('hub-ip-list');
    this.partnerIdInput = document.getElementById('partner-id-input');
    this.partnerPassInput = document.getElementById('partner-password-input');
    this.btnConnectPartner = document.getElementById('btn-connect-partner');
    this.connectError = document.getElementById('connect-error');

    // Session
    this.sessionStatusDot = document.getElementById('session-status-dot');
    this.sessionLatencyBadge = document.getElementById('session-latency-badge');
    this.sessionFpsBadge = document.getElementById('session-fps-badge');
    this.sessionBandwidthBadge = document.getElementById('session-bandwidth-badge');
    this.recordingBadge = document.getElementById('recording-badge');
    this.sessionMonitorSelect = document.getElementById('session-monitor-select');
    this.sessionQualitySelect = document.getElementById('session-quality-select');
    this.sessionActionsDropdown = document.getElementById('session-actions-dropdown');
    this.chatDrawer = document.getElementById('chat-drawer');
    this.vkDrawer = document.getElementById('vk-drawer');
    this.chatMessages = document.getElementById('chat-messages');

    // Modals
    this.clipboardModal = document.getElementById('clipboard-modal');
    this.contactModal = document.getElementById('contact-modal');
  }

  bindEvents() {
    // Theme toggle
    document.getElementById('theme-toggle-btn').addEventListener('click', () => this.toggleTheme());

    // Copy ID & PIN
    document.getElementById('btn-copy-id').addEventListener('click', () => {
      const id = this.hostMachineId.innerText.trim();
      navigator.clipboard.writeText(id).then(() => this.showToast('Partner ID copied', 'success'));
    });
    document.getElementById('btn-copy-pin').addEventListener('click', () => {
      const pin = this.hostPinDisplay.innerText.trim();
      navigator.clipboard.writeText(pin).then(() => this.showToast('One-Time PIN copied', 'success'));
    });
    document.getElementById('btn-regenerate-pin').addEventListener('click', () => this.regeneratePin());

    // Permanent Password Configuration
    const btnSetPermPwd = document.getElementById('btn-set-perm-pwd');
    if (btnSetPermPwd) {
      btnSetPermPwd.addEventListener('click', () => this.promptPermanentPassword());
    }

    // Windows Auto-Start Toggle
    const autostartToggle = document.getElementById('autostart-toggle');
    if (autostartToggle) {
      autostartToggle.addEventListener('change', (e) => this.toggleWindowsAutoStart(e.target.checked));
    }

    // Refresh LAN Discovered Peers
    const btnRefreshPeers = document.getElementById('btn-refresh-peers');
    if (btnRefreshPeers) {
      btnRefreshPeers.addEventListener('click', () => {
        this.fetchDiscoveredPeers();
        this.showToast('Scanned LAN & Rendezvous peers', 'info');
      });
    }

    // Quick Connect Pills
    document.querySelectorAll('.quick-host-pill').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        this.partnerIdInput.value = e.target.getAttribute('data-host');
        this.partnerPassInput.focus();
      });
    });

    // Connect Form
    document.getElementById('connect-form').addEventListener('submit', (e) => {
      e.preventDefault();
      this.handleConnect();
    });

    // Session Toolbar Events
    document.getElementById('btn-session-audio').addEventListener('click', () => {
      this.toggleRemoteAudio();
    });
    document.getElementById('btn-session-privacy').addEventListener('click', () => {
      this.togglePrivacyMode();
    });
    document.getElementById('btn-session-cursor').addEventListener('click', (e) => {
      this.renderer.showRemoteCursor = !this.renderer.showRemoteCursor;
      e.currentTarget.classList.toggle('active', this.renderer.showRemoteCursor);
      this.showToast(`Remote cursor ${this.renderer.showRemoteCursor ? 'shown' : 'hidden'}`, 'info');
    });
    document.getElementById('btn-session-pointer-lock').addEventListener('click', () => {
      this.input.togglePointerLock();
    });
    document.getElementById('btn-session-fullscreen').addEventListener('click', () => {
      this.toggleFullscreen();
    });
    document.getElementById('btn-session-screenshot').addEventListener('click', () => {
      this.takeScreenshot();
    });
    document.getElementById('btn-session-record').addEventListener('click', () => {
      this.toggleSessionRecording();
    });
    document.getElementById('btn-session-chat').addEventListener('click', () => {
      this.chatDrawer.classList.toggle('hidden');
    });
    document.getElementById('btn-close-chat').addEventListener('click', () => {
      this.chatDrawer.classList.add('hidden');
    });
    document.getElementById('btn-session-vk').addEventListener('click', () => {
      this.vkDrawer.classList.toggle('hidden');
    });
    document.getElementById('btn-close-vk').addEventListener('click', () => {
      this.vkDrawer.classList.add('hidden');
    });
    document.getElementById('btn-session-clipboard').addEventListener('click', () => {
      this.openClipboardModal();
    });
    document.getElementById('btn-session-disconnect').addEventListener('click', () => {
      this.disconnectSession();
    });

    // Macro Actions dropdown
    document.getElementById('btn-session-actions').addEventListener('click', (e) => {
      e.stopPropagation();
      this.sessionActionsDropdown.classList.toggle('hidden');
    });
    document.addEventListener('click', () => {
      this.sessionActionsDropdown.classList.add('hidden');
    });
    document.querySelectorAll('[data-action]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const action = e.currentTarget.getAttribute('data-action');
        this.triggerSpecialAction(action);
      });
    });

    // In-Session Chat Form
    document.getElementById('chat-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const input = document.getElementById('chat-input');
      const text = input.value.trim();
      if (text) {
        this.sendWs({ type: 'chat', text, sender: 'Partner' });
        input.value = '';
      }
    });

    // Monitor & Quality presets
    document.getElementById('session-monitor-select').addEventListener('change', (e) => {
      const monIdx = parseInt(e.target.value, 10);
      this.sendWs({ type: 'config', monitor: monIdx });
      this.showToast(`Switched to Display ${monIdx}`, 'success');
    });
    document.getElementById('session-quality-select').addEventListener('change', (e) => {
      this.applyQualityPreset(e.target.value);
    });

    // Clipboard Modal Buttons
    document.getElementById('btn-copy-to-remote').addEventListener('click', () => {
      const text = document.getElementById('clipboard-textarea').value;
      this.sendWs({ type: 'clipboard_set', text });
      this.showToast('Pushed text to remote clipboard', 'success');
      this.clipboardModal.classList.add('hidden');
    });
    document.getElementById('btn-fetch-host-clip').addEventListener('click', async () => {
      const text = await this.fetchHostClipboard();
      document.getElementById('clipboard-textarea').value = text;
      this.showToast('Fetched host clipboard', 'success');
    });

    // Modals Close
    document.querySelectorAll('.modal-close').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const overlay = e.target.closest('.modal-overlay');
        if (overlay) overlay.classList.add('hidden');
      });
    });

    // Address Book Modal Trigger
    document.getElementById('btn-add-contact').addEventListener('click', () => {
      this.contactModal.classList.remove('hidden');
    });
    document.getElementById('contact-form').addEventListener('submit', (e) => {
      e.preventDefault();
      this.saveContact();
    });

    // TCP Tunnel Form
    const tunnelForm = document.getElementById('tunnel-form');
    if (tunnelForm) {
      tunnelForm.addEventListener('submit', (e) => {
        e.preventDefault();
        this.createTunnel();
      });
    }
    const btnRefreshTunnels = document.getElementById('btn-refresh-tunnels');
    if (btnRefreshTunnels) {
      btnRefreshTunnels.addEventListener('click', () => this.loadTunnels());
    }

    // Security & Settings Form
    const btnSavePerms = document.getElementById('btn-save-permissions');
    if (btnSavePerms) {
      btnSavePerms.addEventListener('click', () => this.saveSecurityPermissions());
    }
    const btnRefreshLog = document.getElementById('btn-refresh-security-log');
    if (btnRefreshLog) {
      btnRefreshLog.addEventListener('click', () => this.loadSecuritySettings());
    }

    document.getElementById('hub-setting-quality').addEventListener('input', (e) => {
      document.getElementById('hub-val-quality').innerText = `${e.target.value}%`;
    });
    document.getElementById('hub-setting-fps').addEventListener('input', (e) => {
      document.getElementById('hub-val-fps').innerText = `${e.target.value} FPS`;
    });
    document.getElementById('btn-save-hub-settings').addEventListener('click', () => {
      const quality = parseInt(document.getElementById('hub-setting-quality').value, 10);
      const fps = parseInt(document.getElementById('hub-setting-fps').value, 10);
      const format = document.getElementById('hub-setting-format').value;
      const scaling = document.getElementById('hub-setting-scaling').value;
      const skipStatic = document.getElementById('hub-setting-skip-static').checked;
      const mapCmd = document.getElementById('hub-setting-map-cmd').checked;

      this.renderer.setScalingMode(scaling);
      this.input.mapCmdToCtrl = mapCmd;
      this.sendWs({ type: 'config', quality, fps, format, skip_static: skipStatic });
      this.showToast('Stream configuration applied', 'success');
    });
  }

  // ── SUITE TAB NAVIGATION ───────────────────────────────────────────────────
  bindSuiteTabs() {
    document.querySelectorAll('.hub-nav-tab').forEach((tab) => {
      tab.addEventListener('click', (e) => {
        const target = e.currentTarget.getAttribute('data-tab');
        document.querySelectorAll('.hub-nav-tab').forEach((t) => t.classList.remove('active'));
        e.currentTarget.classList.add('active');

        document.querySelectorAll('.hub-tab-pane').forEach((p) => p.classList.add('hidden'));

        const pane = document.getElementById(`pane-${target}`);
        if (pane) {
          pane.classList.remove('hidden');
        }

        if (this.diagInterval) {
          clearInterval(this.diagInterval);
          this.diagInterval = null;
        }

        if (target === 'files') {
          this.initFileManager();
        } else if (target === 'tunnels') {
          this.loadTunnels();
        } else if (target === 'terminal') {
          this.initTerminal();
        } else if (target === 'diagnostics') {
          this.initDiagnostics();
        } else if (target === 'settings') {
          this.loadSecuritySettings();
        }
      });
    });
  }

  // ── HOST DISCOVERY, RENDEZVOUS & AUTHENTICATION ─────────────────────────────
  async fetchHostInfo() {
    try {
      const res = await fetch('/api/host-info');
      if (res.ok) {
        const data = await res.json();
        this.hostMachineId.innerText = data.machine_id || '349 443 493';
        this.hostPinDisplay.innerText = data.pin || '••••••';

        if (data.monitor_rect) {
          const m = data.monitor_rect;
          this.hostDisplayInfo.innerText = `${m.width}x${m.height} (Display ${data.active_monitor})`;
        }

        if (data.local_ips && data.local_ips.length > 0) {
          const lanIps = data.local_ips.filter((ip) => ip !== '127.0.0.1' && ip !== '::1');
          if (lanIps.length > 0) {
            this.hubIpList.innerText = `LAN: ${lanIps.join(', ')}:${window.location.port || 8000}`;
          }
        }

        const permBadge = document.getElementById('perm-pwd-badge');
        if (permBadge) {
          permBadge.innerText = data.has_permanent_password ? 'Active (Unattended)' : 'Not Set';
          permBadge.style.color = data.has_permanent_password ? 'var(--green)' : 'var(--text-muted)';
        }

        if (data.windows_integration) {
          const autoToggle = document.getElementById('autostart-toggle');
          if (autoToggle) autoToggle.checked = !!data.windows_integration.autostart_enabled;
        }

        const isHttps = window.location.protocol === 'https:';
        const secBadge = document.getElementById('hub-security-badge');
        if (secBadge) {
          secBadge.innerText = isHttps ? 'HTTPS (TLS) + UDP 21116' : 'HTTP (Local) + UDP 21116';
          secBadge.style.color = isHttps ? 'var(--green)' : 'var(--accent)';
        }

        if (data.monitors) {
          this.sessionMonitorSelect.innerHTML = '';
          data.monitors.forEach((mon) => {
            const opt = document.createElement('option');
            opt.value = mon.index;
            opt.innerText = mon.label;
            if (mon.index === data.active_monitor) opt.selected = true;
            this.sessionMonitorSelect.appendChild(opt);
          });
        }
      }
    } catch (e) {
      console.debug('Host info check:', e);
    }
  }

  async fetchDiscoveredPeers() {
    try {
      const res = await fetch('/api/discovery/peers');
      if (!res.ok) return;
      const data = await res.json();
      const grid = document.getElementById('discovered-peers-grid');
      if (!grid) return;
      grid.innerHTML = '';

      const peers = data.peers || [];
      if (peers.length === 0) {
        grid.innerHTML = `<div style="color:var(--text-muted);font-size:12px;">Scanning local network on UDP 21116...</div>`;
        return;
      }

      peers.forEach((peer) => {
        const card = document.createElement('div');
        card.className = 'addr-card';
        card.style.padding = '12px 16px';
        const selfTag = peer.is_self ? `<span style="color:var(--accent);font-size:11px;font-weight:700;">(This PC)</span>` : '';
        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <div>
              <div style="font-weight:700;font-size:14px;">🖥️ ${this.escapeHtml(peer.hostname)} ${selfTag}</div>
              <div class="font-mono text-muted" style="font-size:12px;">ID: <strong>${this.escapeHtml(peer.machine_id)}</strong> • ${this.escapeHtml(peer.ip)}:${peer.port}</div>
            </div>
            <button class="btn-secondary btn-peer-connect" style="padding:5px 12px;font-size:12px;">Connect</button>
          </div>
        `;
        card.querySelector('.btn-peer-connect').addEventListener('click', () => {
          this.partnerIdInput.value = peer.machine_id;
          this.partnerPassInput.focus();
          this.showToast(`Selected ${peer.hostname} (${peer.machine_id})`, 'info');
        });
        grid.appendChild(card);
      });
    } catch (e) {
      console.debug('Peer discovery error:', e);
    }
  }

  async ensureAuthenticated() {
    if (this.token) return true;
    const currentPin = this.hostPinDisplay.innerText.trim();
    if (currentPin && currentPin !== '••••••') {
      try {
        const res = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pin: currentPin }),
        });
        if (res.ok) {
          const data = await res.json();
          this.token = data.token;
          localStorage.setItem('rd_session_token', this.token);
          return true;
        }
      } catch (e) {}
    }
    return false;
  }

  async promptPermanentPassword() {
    await this.ensureAuthenticated();
    const pwd = prompt('Enter a Permanent Password for Unattended Access (or leave empty to clear):');
    if (pwd === null) return;
    try {
      const res = await fetch('/api/security/permanent-password', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.token}`,
        },
        body: JSON.stringify({ password: pwd }),
      });
      if (res.ok) {
        const data = await res.json();
        this.showToast(
          data.has_permanent_password
            ? 'Permanent Unattended Password saved!'
            : 'Permanent Password cleared.',
          'success'
        );
        this.fetchHostInfo();
      } else {
        this.showToast('Authenticate with host PIN first to set password', 'warning');
      }
    } catch (e) {
      this.showToast('Failed to update permanent password', 'error');
    }
  }

  async toggleWindowsAutoStart(enabled) {
    await this.ensureAuthenticated();
    try {
      const res = await fetch('/api/windows/autostart', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.token}`,
        },
        body: JSON.stringify({ enabled }),
      });
      if (res.ok) {
        this.showToast(
          enabled
            ? 'Registered RemoteDesk Pro to start automatically with Windows'
            : 'Removed from Windows startup',
          'success'
        );
      }
    } catch (e) {
      this.showToast('Failed to update Windows startup setting', 'error');
    }
  }

  async regeneratePin() {
    await this.ensureAuthenticated();
    try {
      const res = await fetch('/api/auth/regenerate-pin', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${this.token}` },
      });
      if (res.ok) {
        const data = await res.json();
        this.hostPinDisplay.innerText = data.pin;
        this.showToast('Regenerated new One-Time PIN', 'success');
      } else {
        this.showToast('Please authenticate first to regenerate PIN', 'warning');
      }
    } catch (e) {
      this.showToast('Unable to regenerate PIN', 'error');
    }
  }

  // ── CONNECT TO REMOTE PARTNER (WITH 9-DIGIT PARTNER ID RESOLUTION) ──────────
  async handleConnect() {
    const partnerId = this.partnerIdInput.value.trim();
    const password = this.partnerPassInput.value.trim();

    if (!password) {
      this.connectError.innerText = 'Please enter the Partner One-Time PIN or Permanent Password.';
      return;
    }

    this.connectError.innerText = '';
    this.btnConnectPartner.disabled = true;
    this.btnConnectPartner.innerHTML = `<span>⏳</span> Connecting...`;

    try {
      let targetBaseUrl = window.location.origin;

      // 1. If user entered IP:Port or URL directly
      if (partnerId.includes(':') || partnerId.includes('.') || partnerId.toLowerCase().startsWith('localhost')) {
        if (!partnerId.startsWith('http://') && !partnerId.startsWith('https://')) {
          targetBaseUrl = `${window.location.protocol}//${partnerId}`;
        } else {
          targetBaseUrl = partnerId;
        }
      } else if (partnerId) {
        // 2. User entered a 9-digit RustDesk-style Partner ID -> Resolve via LAN Discovery & Rendezvous Server
        try {
          const resLookup = await fetch(`/api/discovery/resolve?partner_id=${encodeURIComponent(partnerId)}`);
          if (resLookup.ok) {
            const lookupData = await resLookup.json();
            if (lookupData.found && lookupData.url) {
              targetBaseUrl = lookupData.url;
            }
          }
        } catch (e) {
          console.debug('Partner ID lookup fallback to origin:', e);
        }
      }

      const res = await fetch(`${targetBaseUrl}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin: password, password }),
      });

      const data = await res.json();
      if (res.ok && data.success) {
        this.token = data.token;
        this.activeHostUrl = targetBaseUrl;
        localStorage.setItem('rd_session_token', this.token);

        const mode = document.querySelector('input[name="conn-mode"]:checked').value;
        this.input.setViewOnly(mode === 'view-only');

        this.switchToSessionView();
        this.connectWebSocket();
      } else {
        this.connectError.innerText = data.detail || 'Authentication failed. Incorrect PIN or Password.';
      }
    } catch (err) {
      this.connectError.innerText = `Connection failed: ${err.message}`;
    } finally {
      this.btnConnectPartner.disabled = false;
      this.btnConnectPartner.innerHTML = `<span>🚀</span> Connect to Partner`;
    }
  }

  switchToSessionView() {
    this.hubView.classList.add('hidden');
    this.sessionView.classList.remove('hidden');
    this.renderer.resizeCanvas();
  }

  switchToHubView() {
    this.sessionView.classList.add('hidden');
    this.hubView.classList.remove('hidden');
    this.input.setEnabled(false);
  }

  // ── WEBSOCKET STREAMING ENGINE ──────────────────────────────────────────────
  connectWebSocket() {
    if (this.ws) this.ws.close();

    const isSecure = this.activeHostUrl.startsWith('https:');
    const wsProto = isSecure ? 'wss:' : 'ws:';
    const hostWithPort = this.activeHostUrl.replace(/^https?:\/\//, '');
    const wsUrl = `${wsProto}//${hostWithPort}/ws/stream?token=${encodeURIComponent(this.token)}`;

    this.sessionStatusDot.className = 'status-dot connecting';
    this.ws = new WebSocket(wsUrl);
    this.ws.binaryType = 'blob';

    this.ws.onopen = () => {
      this.connected = true;
      this.sessionStatusDot.className = 'status-dot';
      this.input.setEnabled(true);
      this.showToast('Connected to Remote Desktop', 'success');
      this.startPingLoop();
      this.fetchHostInfo();
    };

    this.ws.onmessage = async (event) => {
      if (typeof event.data === 'string') {
        this.handleTextMessage(JSON.parse(event.data));
      } else if (event.data instanceof Blob) {
        this.handleBinaryFrame(event.data);
      }
    };

    this.ws.onclose = () => {
      this.connected = false;
      this.sessionStatusDot.className = 'status-dot offline';
      this.input.setEnabled(false);
      this.showToast('Remote session disconnected', 'warning');
    };
  }

  sendWs(msg) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  handleTextMessage(msg) {
    if (msg.type === 'frame_meta') {
      this.lastFrameMeta = msg;
      this.renderer.setRemoteResolution(msg.width, msg.height);
      if (typeof msg.cursor_x === 'number' && typeof msg.cursor_y === 'number') {
        this.renderer.setRemoteCursor(msg.cursor_x, msg.cursor_y);
      }
    } else if (msg.type === 'pong') {
      const now = performance.now();
      const rtt = Math.round(now - msg.t);
      this.currentLatency = rtt;
      this.sessionLatencyBadge.innerText = `${rtt} ms`;
    } else if (msg.type === 'audio_frame') {
      this.playAudioFrame(msg.data_b64);
    } else if (msg.type === 'privacy_mode_status') {
      this.privacyModeEnabled = !!msg.enabled;
      const btn = document.getElementById('btn-session-privacy');
      if (btn) btn.classList.toggle('active', this.privacyModeEnabled);
      this.showToast(
        this.privacyModeEnabled
          ? '🕵️ Privacy Mode ENABLED (Remote screen blanked)'
          : 'Privacy Mode disabled',
        'info'
      );
    } else if (msg.type === 'audio_status') {
      this.audioMuted = !!msg.muted;
      const btn = document.getElementById('btn-session-audio');
      if (btn) {
        btn.classList.toggle('active', !this.audioMuted);
        btn.innerHTML = this.audioMuted ? `<span>🔇</span> Audio` : `<span>🔊</span> Audio`;
      }
    } else if (msg.type === 'chat_message') {
      this.appendChatMessage(msg.sender, msg.text, msg.time);
    } else if (msg.type === 'clipboard_sync') {
      this.showToast('Remote clipboard updated', 'info');
    }
  }

  async handleBinaryFrame(blob) {
    this.bandwidthBytes += blob.size;
    try {
      const bitmap = await createImageBitmap(blob);
      this.renderer.renderFrame(bitmap);
      bitmap.close();

      this.fpsCounter++;

      if (this.lastFrameMeta && this.lastFrameMeta.seq) {
        this.sendWs({ type: 'ack', seq: this.lastFrameMeta.seq });
      }
    } catch (e) {
      console.warn('Frame render error:', e);
    }
  }

  startPingLoop() {
    setInterval(() => {
      if (this.connected) {
        this.sendWs({ type: 'ping', t: performance.now(), last_rtt: this.currentLatency });
      }
    }, 2000);
  }

  startMetricsTimer() {
    setInterval(() => {
      this.currentFps = this.fpsCounter;
      this.fpsCounter = 0;
      this.sessionFpsBadge.innerText = `${this.currentFps} FPS`;

      this.currentBandwidthKbps = Math.round(this.bandwidthBytes / 1024);
      this.bandwidthBytes = 0;
      if (this.currentBandwidthKbps > 1024) {
        this.sessionBandwidthBadge.innerText = `${(this.currentBandwidthKbps / 1024).toFixed(1)} MB/s`;
      } else {
        this.sessionBandwidthBadge.innerText = `${this.currentBandwidthKbps} KB/s`;
      }
    }, 1000);
  }

  // ── RUSTDESK REMOTE AUDIO & PRIVACY MODE ────────────────────────────────────
  toggleRemoteAudio() {
    this.audioMuted = !this.audioMuted;
    if (!this.audioMuted && !this.audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        this.audioCtx = new AudioContextClass();
      }
    }
    this.sendWs({ type: 'audio_toggle', muted: this.audioMuted });
  }

  async playAudioFrame(b64Data) {
    if (this.audioMuted || !this.audioCtx || !b64Data) return;
    try {
      const binaryStr = atob(b64Data);
      const len = binaryStr.length;
      const bytes = new Uint8Array(len);
      for (let i = 0; i < len; i++) {
        bytes[i] = binaryStr.charCodeAt(i);
      }
      const audioBuffer = await this.audioCtx.decodeAudioData(bytes.buffer);
      const source = this.audioCtx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(this.audioCtx.destination);
      source.start(0);
    } catch (e) {
      console.debug('Audio decode skipped:', e);
    }
  }

  togglePrivacyMode() {
    this.privacyModeEnabled = !this.privacyModeEnabled;
    this.sendWs({ type: 'privacy_mode', enabled: this.privacyModeEnabled });
  }

  // ── IN-SESSION TOOLS: SCREENSHOT, RECORDING, CHAT ───────────────────────────
  takeScreenshot() {
    try {
      const dataUrl = this.canvas.toDataURL('image/png');
      const a = document.createElement('a');
      a.href = dataUrl;
      a.download = `RemoteDesktop_Screenshot_${Date.now()}.png`;
      a.click();
      this.showToast('Screenshot saved to Downloads', 'success');
    } catch (e) {
      this.showToast('Failed to save screenshot', 'error');
    }
  }

  toggleSessionRecording() {
    const recordBtn = document.getElementById('btn-session-record');
    if (!this.isRecording) {
      try {
        const stream = this.canvas.captureStream(30);
        this.recordedChunks = [];
        this.mediaRecorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp8' });

        this.mediaRecorder.ondataavailable = (e) => {
          if (e.data.size > 0) this.recordedChunks.push(e.data);
        };

        this.mediaRecorder.onstop = () => {
          const blob = new Blob(this.recordedChunks, { type: 'video/webm' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `RemoteSession_Recording_${Date.now()}.webm`;
          a.click();
          this.showToast('Session recording saved to Downloads', 'success');
        };

        this.mediaRecorder.start(1000);
        this.isRecording = true;
        this.recordingBadge.classList.remove('hidden');
        recordBtn.classList.add('active');
        recordBtn.innerHTML = `<span>⏹️</span> Stop`;
        this.showToast('Session recording started', 'info');
      } catch (e) {
        this.showToast('Screen recording not supported in this browser', 'error');
      }
    } else {
      if (this.mediaRecorder) {
        this.mediaRecorder.stop();
      }
      this.isRecording = false;
      this.recordingBadge.classList.add('hidden');
      recordBtn.classList.remove('active');
      recordBtn.innerHTML = `<span>⏺️</span> Rec`;
    }
  }

  appendChatMessage(sender, text, time) {
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${sender === 'Client' || sender === 'Partner' ? 'mine' : 'remote'}`;
    bubble.innerHTML = `<strong>${sender}:</strong> ${this.escapeHtml(text)} <span style="font-size:10px;opacity:0.6;margin-left:6px;">${time}</span>`;
    this.chatMessages.appendChild(bubble);
    this.chatMessages.scrollTop = this.chatMessages.scrollHeight;

    if (this.chatDrawer.classList.contains('hidden')) {
      this.showToast(`New chat message from ${sender}`, 'info');
    }
  }

  async uploadDroppedFiles(files) {
    await this.ensureAuthenticated();
    if (!this.desktopUploadPath) {
      try {
        const res = await fetch(`${this.activeHostUrl}/api/files/desktop-path`, {
          headers: { 'Authorization': `Bearer ${this.token}` },
        });
        const data = await res.json();
        this.desktopUploadPath = data.desktop_path || '';
      } catch (e) {}
    }

    const targetDir = this.desktopUploadPath || '.';
    this.showToast(`Uploading ${files.length} file(s) to Remote Desktop...`, 'info');

    for (const file of files) {
      const formData = new FormData();
      formData.append('target_dir', targetDir);
      formData.append('file', file);

      try {
        const res = await fetch(`${this.activeHostUrl}/api/files/upload`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${this.token}` },
          body: formData,
        });
        if (res.ok) {
          this.showToast(`Uploaded: ${file.name}`, 'success');
        }
      } catch (e) {
        this.showToast(`Failed to upload ${file.name}`, 'error');
      }
    }
  }

  // ── REMOTE FILE MANAGER + FOLDER ZIP ARCHIVE ────────────────────────────────
  async initFileManager() {
    await this.ensureAuthenticated();
    try {
      const res = await fetch(`${this.activeHostUrl}/api/files/drives`, {
        headers: { 'Authorization': `Bearer ${this.token}` },
      });
      if (res.ok) {
        const data = await res.json();
        const drivesBar = document.getElementById('fm-drives-list');
        drivesBar.innerHTML = '';

        const allItems = [...(data.shortcuts || []), ...(data.drives || [])];
        allItems.forEach((d) => {
          const btn = document.createElement('button');
          btn.className = 'fm-drive-btn';
          btn.innerHTML = `<span>💾</span> ${d.name || d.label}`;
          btn.addEventListener('click', () => this.loadDirectory(d.path));
          drivesBar.appendChild(btn);
        });

        this.loadDirectory(data.default_path || '');
      } else {
        this.showToast('Please authenticate to view remote files', 'warning');
      }
    } catch (e) {
      this.showToast('Failed to load file drives', 'error');
    }

    document.getElementById('btn-fm-up').onclick = () => {
      if (this.parentFmPath) this.loadDirectory(this.parentFmPath);
    };
    document.getElementById('btn-fm-refresh').onclick = () => {
      this.loadDirectory(this.currentFmPath);
    };
    document.getElementById('btn-fm-zip-current').onclick = () => {
      if (this.currentFmPath) this.downloadFolderZip(this.currentFmPath);
    };
    document.getElementById('fm-file-input').onchange = async (e) => {
      if (e.target.files && e.target.files.length > 0) {
        for (const f of e.target.files) {
          await this.uploadFmFile(f);
        }
      }
    };
    document.getElementById('btn-fm-new-folder').onclick = () => {
      const name = prompt('Enter new folder name:');
      if (name) this.createFmFolder(name);
    };
  }

  async loadDirectory(dirPath) {
    try {
      const res = await fetch(`${this.activeHostUrl}/api/files/list?path=${encodeURIComponent(dirPath)}`, {
        headers: { 'Authorization': `Bearer ${this.token}` },
      });
      if (res.ok) {
        const data = await res.json();
        this.currentFmPath = data.current_path;
        this.parentFmPath = data.parent_path;
        document.getElementById('fm-current-path').innerText = data.current_path;

        const tbody = document.getElementById('fm-table-body');
        tbody.innerHTML = '';

        data.items.forEach((item) => {
          const tr = document.createElement('tr');
          tr.className = 'fm-row';
          const icon = item.is_dir ? '📁' : '📄';
          const sizeStr = item.is_dir ? 'Folder' : this.formatBytes(item.size);
          const dateStr = new Date(item.modified * 1000).toLocaleDateString();

          tr.innerHTML = `
            <td><span>${icon}</span> <strong>${this.escapeHtml(item.name)}</strong></td>
            <td class="font-mono text-muted">${sizeStr}</td>
            <td class="text-muted">${dateStr}</td>
            <td style="text-align: right;">
              ${item.is_dir
                ? `<button class="icon-action-btn btn-zip" title="Download Folder as .ZIP">📦</button>`
                : `<button class="icon-action-btn btn-dl" title="Download File">⬇️</button>`}
              <button class="icon-action-btn btn-del" title="Delete" style="color:var(--red);">🗑️</button>
            </td>
          `;

          tr.addEventListener('click', (e) => {
            if (e.target.closest('.icon-action-btn')) return;
            if (item.is_dir) {
              this.loadDirectory(item.path);
            }
          });

          const dlBtn = tr.querySelector('.btn-dl');
          if (dlBtn) {
            dlBtn.addEventListener('click', () => {
              window.open(`${this.activeHostUrl}/api/files/download?path=${encodeURIComponent(item.path)}&token=${encodeURIComponent(this.token)}`);
            });
          }

          const zipBtn = tr.querySelector('.btn-zip');
          if (zipBtn) {
            zipBtn.addEventListener('click', () => {
              this.downloadFolderZip(item.path);
            });
          }

          tr.querySelector('.btn-del').addEventListener('click', async () => {
            if (confirm(`Are you sure you want to delete ${item.name}?`)) {
              await fetch(`${this.activeHostUrl}/api/files/delete`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.token}` },
                body: JSON.stringify({ path: item.path }),
              });
              this.loadDirectory(this.currentFmPath);
            }
          });

          tbody.appendChild(tr);
        });
      }
    } catch (e) {
      this.showToast('Failed to list files', 'error');
    }
  }

  downloadFolderZip(folderPath) {
    this.showToast('Compressing folder to .zip archive...', 'info');
    window.open(`${this.activeHostUrl}/api/files/download-zip?path=${encodeURIComponent(folderPath)}&token=${encodeURIComponent(this.token)}`);
  }

  async uploadFmFile(file) {
    const queueBar = document.getElementById('fm-transfer-queue');
    const queueStatus = document.getElementById('fm-transfer-status');
    if (queueBar && queueStatus) {
      queueBar.classList.remove('hidden');
      queueStatus.innerText = `Uploading ${file.name} (${this.formatBytes(file.size)})...`;
    }

    const formData = new FormData();
    formData.append('target_dir', this.currentFmPath);
    formData.append('file', file);

    const startTime = performance.now();
    try {
      const res = await fetch(`${this.activeHostUrl}/api/files/upload`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${this.token}` },
        body: formData,
      });
      if (res.ok) {
        const elapsedSec = Math.max(0.05, (performance.now() - startTime) / 1000);
        const speed = this.formatBytes(Math.round(file.size / elapsedSec)) + '/s';
        if (queueStatus) {
          queueStatus.innerText = `✔ Completed: ${file.name} (${this.formatBytes(file.size)} @ ${speed})`;
        }
        this.showToast(`Uploaded ${file.name}`, 'success');
        this.loadDirectory(this.currentFmPath);
      }
    } catch (e) {
      this.showToast('Upload failed', 'error');
    }
  }

  async createFmFolder(name) {
    try {
      const res = await fetch(`${this.activeHostUrl}/api/files/mkdir`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.token}` },
        body: JSON.stringify({ parent: this.currentFmPath, name }),
      });
      if (res.ok) {
        this.showToast(`Created folder ${name}`, 'success');
        this.loadDirectory(this.currentFmPath);
      }
    } catch (e) {
      this.showToast('Failed to create folder', 'error');
    }
  }

  // ── RUSTDESK TCP PORT TUNNELING ─────────────────────────────────────────────
  async loadTunnels() {
    await this.ensureAuthenticated();
    try {
      const res = await fetch(`${this.activeHostUrl}/api/tunnels`, {
        headers: { 'Authorization': `Bearer ${this.token}` },
      });
      if (!res.ok) return;
      const data = await res.json();
      const tbody = document.getElementById('tunnels-table-body');
      if (!tbody) return;
      tbody.innerHTML = '';

      if (!data.tunnels || data.tunnels.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:20px;">No active TCP tunnels. Create one on the left.</td></tr>`;
        return;
      }

      data.tunnels.forEach((t) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><strong>${this.escapeHtml(t.name)}</strong></td>
          <td class="font-mono" style="color:var(--accent);">127.0.0.1:${t.listen_port}</td>
          <td class="font-mono">${this.escapeHtml(t.target_host)}:${t.target_port}</td>
          <td class="font-mono text-muted">${this.formatBytes(t.bytes_forwarded)} (${t.active_connections} conn)</td>
          <td style="text-align:right;">
            <button class="icon-action-btn btn-stop-tunnel" style="color:var(--red);" title="Stop Tunnel">✕</button>
          </td>
        `;
        tr.querySelector('.btn-stop-tunnel').addEventListener('click', async () => {
          await fetch(`${this.activeHostUrl}/api/tunnels/${t.id}`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${this.token}` },
          });
          this.showToast(`Stopped tunnel :${t.listen_port}`, 'info');
          this.loadTunnels();
        });
        tbody.appendChild(tr);
      });
    } catch (e) {
      console.debug('Tunnel load error:', e);
    }
  }

  async createTunnel() {
    await this.ensureAuthenticated();
    const name = document.getElementById('tunnel-name').value.trim();
    const listenPort = parseInt(document.getElementById('tunnel-listen-port').value, 10);
    const targetPort = parseInt(document.getElementById('tunnel-target-port').value, 10);
    const targetHost = document.getElementById('tunnel-target-host').value.trim() || '127.0.0.1';

    if (isNaN(listenPort) || isNaN(targetPort)) return;

    try {
      const res = await fetch(`${this.activeHostUrl}/api/tunnels`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.token}`,
        },
        body: JSON.stringify({
          name,
          listen_port: listenPort,
          target_host: targetHost,
          target_port: targetPort,
        }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`TCP Tunnel listening on 127.0.0.1:${data.tunnel.listen_port}`, 'success');
        document.getElementById('tunnel-form').reset();
        document.getElementById('tunnel-target-host').value = '127.0.0.1';
        this.loadTunnels();
      } else {
        this.showToast(data.detail || 'Failed to create TCP tunnel', 'error');
      }
    } catch (e) {
      this.showToast('Error creating TCP tunnel', 'error');
    }
  }

  // ── RUSTDESK SECURITY PERMISSIONS & AUDIT LOG ───────────────────────────────
  async loadSecuritySettings() {
    await this.ensureAuthenticated();
    try {
      const [permRes, logRes, sessRes] = await Promise.all([
        fetch('/api/security/permissions'),
        fetch('/api/security/audit-log?limit=20', { headers: { 'Authorization': `Bearer ${this.token}` } }),
        fetch('/api/security/sessions', { headers: { 'Authorization': `Bearer ${this.token}` } }),
      ]);

      if (permRes.ok) {
        const data = await permRes.json();
        const p = data.permissions || {};
        document.getElementById('perm-keyboard-mouse').checked = p.allow_keyboard_mouse !== false;
        document.getElementById('perm-clipboard').checked = p.allow_clipboard !== false;
        document.getElementById('perm-file-transfer').checked = p.allow_file_transfer !== false;
        document.getElementById('perm-audio').checked = p.allow_audio !== false;
        document.getElementById('perm-tcp-tunnel').checked = p.allow_tcp_tunnel !== false;
        document.getElementById('perm-terminal').checked = p.allow_terminal !== false;
        document.getElementById('perm-privacy').checked = p.privacy_mode_allowed !== false;

        if (data.network) {
          document.getElementById('setting-rendezvous-url').value = data.network.rendezvous_server || '';
          document.getElementById('setting-ip-whitelist').value = (data.network.ip_whitelist || []).join(', ');
        }
      }

      const auditContainer = document.getElementById('security-audit-list');
      if (auditContainer) {
        auditContainer.innerHTML = '';

        if (sessRes.ok) {
          const sessData = await sessRes.json();
          (sessData.sessions || []).forEach((s) => {
            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.justifyContent = 'space-between';
            row.style.color = 'var(--green)';
            row.innerHTML = `
              <span>🟢 Active [${s.client_ip}] (${s.auth_method})</span>
              <button style="color:var(--red);font-size:11px;font-weight:700;" data-kick="${s.session_id}">[Kick]</button>
            `;
            row.querySelector('[data-kick]').onclick = async () => {
              await fetch(`/api/security/sessions/${s.session_id}/kick`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${this.token}` },
              });
              this.showToast(`Kicked session ${s.session_id}`, 'warning');
              this.loadSecuritySettings();
            };
            auditContainer.appendChild(row);
          });
        }

        if (logRes.ok) {
          const logData = await logRes.json();
          (logData.events || []).forEach((ev) => {
            const div = document.createElement('div');
            div.innerText = `[${ev.timestamp}] ${ev.event} (${ev.client_ip}) ${ev.details || ''}`;
            auditContainer.appendChild(div);
          });
        }
      }
    } catch (e) {
      console.debug('Security settings load error:', e);
    }
  }

  async saveSecurityPermissions() {
    await this.ensureAuthenticated();
    const perms = {
      allow_keyboard_mouse: document.getElementById('perm-keyboard-mouse').checked,
      allow_clipboard: document.getElementById('perm-clipboard').checked,
      allow_file_transfer: document.getElementById('perm-file-transfer').checked,
      allow_audio: document.getElementById('perm-audio').checked,
      allow_tcp_tunnel: document.getElementById('perm-tcp-tunnel').checked,
      allow_terminal: document.getElementById('perm-terminal').checked,
      privacy_mode_allowed: document.getElementById('perm-privacy').checked,
    };

    const rvUrl = document.getElementById('setting-rendezvous-url').value.trim();
    const whitelistRaw = document.getElementById('setting-ip-whitelist').value.trim();
    const whitelist = whitelistRaw ? whitelistRaw.split(',').map((s) => s.trim()).filter(Boolean) : [];

    try {
      await Promise.all([
        fetch('/api/security/permissions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.token}` },
          body: JSON.stringify(perms),
        }),
        fetch('/api/security/network', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.token}` },
          body: JSON.stringify({ rendezvous_server: rvUrl, ip_whitelist: whitelist }),
        }),
      ]);
      this.showToast('Saved RustDesk security & network policy', 'success');
      this.loadSecuritySettings();
    } catch (e) {
      this.showToast('Failed to save security policy', 'error');
    }
  }

  // ── REMOTE TERMINAL CONSOLE ─────────────────────────────────────────────────
  async initTerminal() {
    await this.ensureAuthenticated();
    if (this.termWs) this.termWs.close();

    const isSecure = this.activeHostUrl.startsWith('https:');
    const wsProto = isSecure ? 'wss:' : 'ws:';
    const hostWithPort = this.activeHostUrl.replace(/^https?:\/\//, '');
    const wsUrl = `${wsProto}//${hostWithPort}/ws/terminal?token=${encodeURIComponent(this.token)}`;

    const out = document.getElementById('terminal-output');
    out.innerText = 'Connecting to Remote Shell...\n';

    this.termWs = new WebSocket(wsUrl);
    this.termWs.onmessage = (e) => {
      out.innerText += e.data;
      out.scrollTop = out.scrollHeight;
    };

    document.getElementById('terminal-form').onsubmit = (e) => {
      e.preventDefault();
      const input = document.getElementById('terminal-input');
      const cmd = input.value;
      if (cmd && this.termWs && this.termWs.readyState === WebSocket.OPEN) {
        this.termWs.send(cmd + '\n');
        input.value = '';
      }
    };

    document.getElementById('btn-term-clear').onclick = () => {
      out.innerText = '';
    };
    document.getElementById('btn-term-reconnect').onclick = () => {
      this.initTerminal();
    };
  }

  // ── TASK MANAGER & TELEMETRY ────────────────────────────────────────────────
  async initDiagnostics() {
    await this.ensureAuthenticated();
    this.pollDiagnostics();
    if (this.diagInterval) clearInterval(this.diagInterval);
    this.diagInterval = setInterval(() => this.pollDiagnostics(), 2500);

    document.querySelectorAll('.btn-power').forEach((btn) => {
      btn.onclick = async (e) => {
        const action = e.target.getAttribute('data-power');
        if (confirm(`Execute power command: ${action.toUpperCase()}?`)) {
          await fetch(`${this.activeHostUrl}/api/system/power`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.token}` },
            body: JSON.stringify({ action }),
          });
          this.showToast(`Executed power action: ${action}`, 'warning');
        }
      };
    });

    document.getElementById('process-search').oninput = (e) => {
      const q = e.target.value.toLowerCase();
      document.querySelectorAll('#process-table-body tr').forEach((tr) => {
        tr.style.display = tr.innerText.toLowerCase().includes(q) ? '' : 'none';
      });
    };
  }

  async pollDiagnostics() {
    try {
      const [telRes, procRes] = await Promise.all([
        fetch(`${this.activeHostUrl}/api/system/telemetry`, { headers: { 'Authorization': `Bearer ${this.token}` } }),
        fetch(`${this.activeHostUrl}/api/system/processes?limit=50&sort=cpu`, { headers: { 'Authorization': `Bearer ${this.token}` } }),
      ]);

      if (telRes.ok) {
        const tel = await telRes.json();
        document.getElementById('diag-cpu-val').innerText = `${Math.round(tel.cpu.percent)}%`;
        document.getElementById('diag-cpu-cores').innerText = `${tel.cpu.cores_logical} Cores`;

        document.getElementById('diag-ram-val').innerText = `${Math.round(tel.memory.percent)}%`;
        document.getElementById('diag-ram-details').innerText = `${(tel.memory.used / (1024**3)).toFixed(1)} / ${(tel.memory.total / (1024**3)).toFixed(1)} GB`;

        const netKb = Math.round(tel.network.bytes_sent_sec + tel.network.bytes_recv_sec) / 1024;
        document.getElementById('diag-net-val').innerText = `${netKb.toFixed(1)} KB/s`;

        const hours = Math.floor(tel.uptime_seconds / 3600);
        const mins = Math.floor((tel.uptime_seconds % 3600) / 60);
        document.getElementById('diag-uptime-val').innerText = `${hours}h ${mins}m`;
        document.getElementById('diag-os-details').innerText = tel.os_name;
      }

      if (procRes.ok) {
        const procData = await procRes.json();
        const tbody = document.getElementById('process-table-body');
        tbody.innerHTML = '';

        procData.processes.forEach((p) => {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td class="font-mono">${p.pid}</td>
            <td><strong>${this.escapeHtml(p.name)}</strong></td>
            <td class="text-muted">${this.escapeHtml(p.user)}</td>
            <td class="font-mono" style="color:var(--accent);">${p.cpu_percent.toFixed(1)}%</td>
            <td class="font-mono">${p.memory_mb} MB</td>
            <td style="text-align:right;">
              <button class="icon-action-btn btn-kill-proc" style="color:var(--red);" title="End Process">✕</button>
            </td>
          `;

          tr.querySelector('.btn-kill-proc').onclick = async () => {
            if (confirm(`Kill process ${p.name} (PID ${p.pid})?`)) {
              await fetch(`${this.activeHostUrl}/api/system/processes/${p.pid}/kill`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${this.token}` },
              });
              this.pollDiagnostics();
            }
          };

          tbody.appendChild(tr);
        });
      }
    } catch (e) {
      console.debug('Diagnostics poll error:', e);
    }
  }

  // ── ADDRESS BOOK ────────────────────────────────────────────────────────────
  loadAddressBook() {
    const raw = localStorage.getItem('rd_addressbook') || '[]';
    const entries = JSON.parse(raw);
    const container = document.getElementById('addressbook-list');
    container.innerHTML = '';

    if (entries.length === 0) {
      container.innerHTML = `<div style="grid-column: 1/-1; color: var(--text-muted); padding: 20px;">No saved machines yet. Click "Add Machine" above.</div>`;
      return;
    }

    entries.forEach((entry, idx) => {
      const card = document.createElement('div');
      card.className = 'addr-card';
      card.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
          <div>
            <div style="font-weight: 700; font-size: 15px;">${this.escapeHtml(entry.alias)}</div>
            <div class="font-mono text-muted" style="font-size: 12px; margin-top: 2px;">${this.escapeHtml(entry.id)}</div>
          </div>
          <button class="icon-action-btn btn-del-addr" style="color: var(--red);">&times;</button>
        </div>
        <button class="btn-primary btn-connect-addr" style="margin-top: 6px;">
          <span>🚀</span> Connect
        </button>
      `;

      card.querySelector('.btn-connect-addr').onclick = () => {
        this.partnerIdInput.value = entry.id;
        if (entry.password) this.partnerPassInput.value = entry.password;
        document.querySelector('.hub-nav-tab[data-tab="remote"]').click();
        this.handleConnect();
      };

      card.querySelector('.btn-del-addr').onclick = () => {
        entries.splice(idx, 1);
        localStorage.setItem('rd_addressbook', JSON.stringify(entries));
        this.loadAddressBook();
      };

      container.appendChild(card);
    });
  }

  saveContact() {
    const alias = document.getElementById('contact-alias').value.trim();
    const id = document.getElementById('contact-id').value.trim();
    const password = document.getElementById('contact-pwd').value.trim();

    if (!alias || !id) return;

    const raw = localStorage.getItem('rd_addressbook') || '[]';
    const entries = JSON.parse(raw);
    entries.push({ alias, id, password });
    localStorage.setItem('rd_addressbook', JSON.stringify(entries));

    this.contactModal.classList.add('hidden');
    this.loadAddressBook();
    this.showToast(`Saved ${alias} to Address Book`, 'success');
  }

  // ── UTILITIES ───────────────────────────────────────────────────────────────
  applyQualityPreset(preset) {
    if (preset === 'auto') {
      this.sendWs({ type: 'config', adaptive: true });
      this.showToast('Enabled RustDesk Auto Adaptive Quality', 'success');
      return;
    }
    let fps = 30, quality = 70;
    if (preset === 'high') { fps = 60; quality = 85; }
    else if (preset === 'balanced') { fps = 30; quality = 70; }
    else if (preset === 'low') { fps = 20; quality = 45; }
    this.sendWs({ type: 'config', adaptive: false, fps, quality });
    this.showToast(`Applied ${preset} stream preset`, 'success');
  }

  triggerSpecialAction(action) {
    this.sendWs({ type: 'special', action });
    this.showToast(`Triggered: ${action}`, 'success');
  }

  async openClipboardModal() {
    this.clipboardModal.classList.remove('hidden');
    const hostText = await this.fetchHostClipboard();
    document.getElementById('clipboard-textarea').value = hostText;
  }

  async fetchHostClipboard() {
    try {
      const res = await fetch(`${this.activeHostUrl}/api/clipboard`, {
        headers: { 'Authorization': `Bearer ${this.token}` },
      });
      const data = await res.json();
      return data.text || '';
    } catch (e) {
      return '';
    }
  }

  toggleFullscreen() {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  }

  disconnectSession() {
    if (this.ws) this.ws.close();
    this.switchToHubView();
    this.showToast('Session ended. Returned to dashboard.', 'info');
  }

  showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerText = message;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 300);
    }, 3000);
  }

  formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
}

// Initialize on DOM load
window.addEventListener('DOMContentLoaded', () => {
  window.app = new RemoteDeskApp();
});
