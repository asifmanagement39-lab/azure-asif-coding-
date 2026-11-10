const toast = document.querySelector('#toast');
let toastTimer;

const currentYear = document.querySelector('#currentYear');
const currentDate = document.querySelector('#currentDate');
const currentTime = document.querySelector('#currentTime');
const greeting = document.querySelector('#greeting');
const localDateFormatter = new Intl.DateTimeFormat('en-US', {
  weekday: 'long',
  month: 'long',
  day: 'numeric'
});
const localTimeFormatter = new Intl.DateTimeFormat('en-US', {
  hour: 'numeric',
  minute: '2-digit',
  second: '2-digit',
  timeZoneName: 'short'
});

function updateLocalDateTime() {
  const now = new Date();
  const hour = now.getHours();
  currentYear.textContent = String(now.getFullYear());
  currentDate.textContent = localDateFormatter.format(now).toUpperCase();
  currentTime.dateTime = now.toISOString();
  currentTime.textContent = localTimeFormatter.format(now);
  greeting.textContent = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
}

updateLocalDateTime();
window.setInterval(updateLocalDateTime, 1000);

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('visible');
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove('visible'), 2800);
}

const streamKeyInput = document.querySelector('#streamKeyInput');
const saveStreamKeyButton = document.querySelector('#saveStreamKeyButton');
const removeStreamKeyButton = document.querySelector('#removeStreamKeyButton');
const streamKeyStatus = document.querySelector('#streamKeyStatus');
const toggleKeyVisibility = document.querySelector('#toggleKeyVisibility');

function setStreamKeyStatus(message, saved = false) {
  streamKeyStatus.textContent = message;
  streamKeyStatus.classList.toggle('saved', saved);
}

async function refreshStreamKeyStatus() {
  try {
    const response = await fetch('/api/stream-key', { cache: 'no-store' });
    if (!response.ok) {
      removeStreamKeyButton.disabled = true;
      setStreamKeyStatus(response.status === 403
        ? 'Set the key in the server .env file'
        : 'Could not load key status');
      return;
    }
    const result = await response.json();
    removeStreamKeyButton.disabled = !result.removable;
    setStreamKeyStatus(
      result.managed_by_environment
        ? 'Managed by server .env'
        : result.configured
          ? 'Saved in server memory'
          : 'No key saved',
      result.managed_by_environment || result.configured
    );
  } catch (error) {
    if (!(error instanceof TypeError)) {
      throw error;
    }
    removeStreamKeyButton.disabled = true;
    setStreamKeyStatus('Local Python service unavailable');
  }
}

saveStreamKeyButton.addEventListener('click', async () => {
  const value = streamKeyInput.value.trim();
  if (!value) {
    showToast('Paste your YouTube stream key first.');
    streamKeyInput.focus();
    return;
  }
  saveStreamKeyButton.disabled = true;
  try {
    const response = await fetch('/api/stream-key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: value }),
      cache: 'no-store'
    });
    if (!response.ok) {
      setStreamKeyStatus('Key was not saved');
      showToast('Local service rejected the key. Check it and try again.');
      return;
    }
    streamKeyInput.value = '';
    streamKeyInput.type = 'password';
    toggleKeyVisibility.textContent = 'Show';
    toggleKeyVisibility.setAttribute('aria-label', 'Show stream key');
    toggleKeyVisibility.setAttribute('aria-pressed', 'false');
    removeStreamKeyButton.disabled = false;
    setStreamKeyStatus('Saved in server memory', true);
    showToast('Stream key saved securely in server memory. Press Start Live when ready.');
    refreshStreamKeyStatus();
    refreshStreamStatus();
  } catch (error) {
    if (!(error instanceof TypeError)) {
      throw error;
    }
    setStreamKeyStatus('Could not reach local Python service');
    showToast('Key was not saved. Start the local Python dashboard service and try again.');
  } finally {
    saveStreamKeyButton.disabled = false;
  }
});

removeStreamKeyButton.addEventListener('click', async () => {
  removeStreamKeyButton.disabled = true;
  try {
    const response = await fetch('/api/stream-key', {
      method: 'DELETE',
      cache: 'no-store'
    });
    if (!response.ok) {
      removeStreamKeyButton.disabled = false;
      showToast('Local service could not remove the key. Try again.');
      return;
    }
    streamKeyInput.value = '';
    streamKeyInput.type = 'password';
    toggleKeyVisibility.textContent = 'Show';
    toggleKeyVisibility.setAttribute('aria-label', 'Show stream key');
    toggleKeyVisibility.setAttribute('aria-pressed', 'false');
    setStreamKeyStatus('No key saved');
    showToast('Stream key removed from local service memory.');
    refreshStreamKeyStatus();
    refreshStreamStatus();
  } catch (error) {
    if (!(error instanceof TypeError)) {
      throw error;
    }
    removeStreamKeyButton.disabled = false;
    showToast('Could not remove the key. Check that the local Python service is running.');
  }
});

toggleKeyVisibility.addEventListener('click', () => {
  const visible = streamKeyInput.type === 'password';
  streamKeyInput.type = visible ? 'text' : 'password';
  toggleKeyVisibility.textContent = visible ? 'Hide' : 'Show';
  toggleKeyVisibility.setAttribute('aria-label', visible ? 'Hide stream key' : 'Show stream key');
  toggleKeyVisibility.setAttribute('aria-pressed', String(visible));
});

refreshStreamKeyStatus();

const startButton = document.querySelector('#startButton');
const streamStatus = document.querySelector('#streamStatus');
const actionHint = document.querySelector('#actionHint');
const previewMessage = document.querySelector('#previewMessage');
const previewDesignMessage = document.querySelector('#previewDesignMessage');
const previewDesignId = document.querySelector('#previewDesignId');
const loopPreviewVideo = document.querySelector('#loopPreviewVideo');
const videoPreview = document.querySelector('.video-preview');
const formatButtons = [...document.querySelectorAll('[data-stream-format]')];
const formatHint = document.querySelector('#formatHint');
const previewResolution = document.querySelector('#previewResolution');
const previewAspect = document.querySelector('#previewAspect');
const encoderStatus = document.querySelector('#encoderStatus');
const encoderStatusDot = document.querySelector('#encoderStatusDot');
const encoderDetails = document.querySelector('#encoderDetails');
const formatDetails = {
  horizontal: { label: 'Horizontal', aspect: '16:9', width: 1280, height: 720 },
  vertical: { label: 'Vertical', aspect: '9:16', width: 720, height: 1280 }
};
let formatSaving = false;
let previewLoading = false;
let liveDesignId = null;
let liveDesignNumber = 0;

async function loadFreshPreview(autoPlay = false) {
  if (previewLoading || !formatDetails[loopPreviewVideo.dataset.format || 'horizontal']) {
    return;
  }
  previewLoading = true;
  previewDesignMessage.textContent = 'Creating a fresh ATG design…';
  try {
    const response = await fetch('/api/preview', {
      method: 'POST',
      cache: 'no-store'
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || 'A fresh ATG preview could not be generated.');
    }

    loopPreviewVideo.dataset.loaded = 'true';
    loopPreviewVideo.dataset.format = result.format;
    loopPreviewVideo.dataset.designId = result.design_id || '';
    previewDesignId.textContent = result.design_id ? `ATG ${result.design_id}` : 'CUSTOM VIDEO';
    previewDesignMessage.textContent = result.generated
      ? 'Rotating 3D ATG hologram · fresh design'
      : 'Custom stream video preview';
    loopPreviewVideo.src = result.video_url;
    loopPreviewVideo.load();
    if (autoPlay) {
      loopPreviewVideo.addEventListener('loadeddata', () => {
        loopPreviewVideo.play().catch((error) => {
          if (!(error instanceof DOMException)) {
            throw error;
          }
          showToast('Use the video controls to resume the fresh preview.');
        });
      }, { once: true });
    }
  } catch (error) {
    previewDesignMessage.textContent = 'Fresh preview is unavailable';
    if (error instanceof TypeError) {
      previewMessage.textContent = 'Cannot reach the preview service';
      return;
    }
    previewMessage.textContent = error.message;
    showToast(error.message);
  } finally {
    previewLoading = false;
  }
}

function updateEncoderMetric(result, format) {
  const details = formatDetails[format] || formatDetails.horizontal;
  const statusLabels = {
    idle: result.configured ? 'Ready' : 'Key needed',
    generating_video: 'Preparing',
    starting: 'Starting',
    streaming: 'Encoder running',
    reconnecting: 'Reconnecting',
    stopping: 'Stopping',
    stopped: 'Offline',
    needs_stream_key: 'Key needed',
    needs_video: 'Video needed',
    video_generation_failed: 'Video error',
    invalid_rtmp_url: 'Check URL',
    ffmpeg_unavailable: 'FFmpeg needed'
  };
  const status = statusLabels[result.state] || 'Checking';
  encoderStatus.textContent = status;
  encoderStatusDot.className = `health-dot ${result.state === 'streaming' ? 'status-streaming' : result.start_requested ? 'status-warning' : 'status-idle'}`;
  const design = result.state === 'streaming' && result.design_id
    ? ` · ATG ${result.design_id} · #${result.design_number}`
    : '';
  encoderDetails.textContent = `${details.width} × ${details.height} · 30 FPS${design}`;
  if (result.design_id && result.design_id !== liveDesignId) {
    liveDesignId = result.design_id;
    liveDesignNumber = result.design_number;
    if (result.state === 'streaming') {
      previewDesignId.textContent = `LIVE ${liveDesignId}`;
      previewDesignMessage.textContent = `Live ATG design #${liveDesignNumber}`;
    }
  }
}

function updateFormatControls(format, locked = false) {
  const details = formatDetails[format] || formatDetails.horizontal;
  for (const button of formatButtons) {
    const selected = button.dataset.streamFormat === format;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', String(selected));
    button.disabled = locked || formatSaving;
  }
  formatHint.textContent = `${details.label} ${details.aspect} selected · ${details.width} × ${details.height}${locked ? ' · Stop stream to change' : ''}`;
  previewResolution.textContent = `${details.width} × ${details.height}`;
  previewAspect.textContent = `${details.aspect} · ${details.label}`;
  videoPreview.classList.toggle('is-vertical', format === 'vertical');
}

const streamStates = {
  idle: ['READY', 'Stream is stopped. Press Start Live when you are ready.', 'Your loop is ready to preview.'],
  streaming: ['STREAMING', 'Fresh ATG graphics are generated continuously while you are live.', 'The server is creating a new design every minute for your stream.'],
  starting: ['STARTING', 'The server is starting the loop stream.', 'Connecting the server stream to YouTube.'],
  generating_video: ['DESIGNING', 'Creating a fresh one-minute ATG cyber animation.', 'Generating your 60-second animated ATG program.'],
  design_generation_failed: ['DESIGN ERROR', 'Could not save a new unique design; check server storage.', 'ATG could not create the next unique design. Check server storage.'],
  reconnecting: ['RECONNECTING', 'The server will retry the stream automatically.', 'Trying to reconnect to YouTube.'],
  needs_stream_key: ['KEY NEEDED', 'Set YOUTUBE_STREAM_KEY in the server .env file.', 'Add the YouTube stream key to the server environment.'],
  needs_video: ['VIDEO NEEDED', 'Add your loop video at media/stream.mp4 on the server.', 'The server is waiting for media/stream.mp4.'],
  video_generation_failed: ['VIDEO ERROR', 'Check the server logs and confirm FFmpeg is installed.', 'The server could not create the graphic loop.'],
  invalid_rtmp_url: ['CHECK URL', 'Set a valid RTMP or RTMPS ingest URL on the server.', 'The YouTube ingest address is invalid.'],
  ffmpeg_unavailable: ['FFMPEG NEEDED', 'Install FFmpeg or use the provided Docker image.', 'The server could not start FFmpeg.'],
  stopped: ['STOPPED', 'Start the Python service to run the automatic loop.', 'The stream service is stopped.']
};

async function refreshStreamStatus() {
  try {
    const response = await fetch('/api/stream-status', { cache: 'no-store' });
    if (!response.ok) {
      throw new TypeError('Stream status request failed.');
    }
    const result = await response.json();
    const selectedFormat = result.format in formatDetails ? result.format : 'horizontal';
    updateEncoderMetric(result, selectedFormat);
    const previewFormatChanged = loopPreviewVideo.dataset.format !== selectedFormat;
    updateFormatControls(selectedFormat, result.start_requested);
    if (previewFormatChanged && loopPreviewVideo.dataset.loaded) {
      delete loopPreviewVideo.dataset.loaded;
      delete loopPreviewVideo.dataset.designId;
      loopPreviewVideo.removeAttribute('src');
      loopPreviewVideo.load();
      videoPreview.classList.remove('has-preview');
    }
    let [label, hint, message] = streamStates[result.state] || streamStates.starting;
    if (!result.configured && !result.start_requested) {
      label = 'KEY NEEDED';
      hint = 'Save your YouTube stream key to the server first.';
      message = 'Add your stream key, then press Start Live.';
    } else if (result.state === 'idle' && result.configured) {
      label = 'READY';
      hint = 'Your generated loop is ready. Press Start Live to broadcast.';
      message = 'Ready when you are';
    }
    if (result.last_error) {
      hint = result.last_error;
      message = result.last_error;
    }
    streamStatus.classList.toggle('rehearsing', result.state === 'streaming');
    streamStatus.innerHTML = `<i></i> ${label}`;
    const retryableStates = ['ffmpeg_unavailable', 'video_generation_failed', 'design_generation_failed', 'invalid_rtmp_url', 'needs_video'];
    const canRetry = result.start_requested && retryableStates.includes(result.state);
    const isBusy = result.start_requested && !canRetry;
    const buttonLabel = result.state === 'streaming'
      ? 'Live is running'
      : canRetry
        ? 'Retry Start'
        : isBusy
          ? 'Starting...'
          : 'Start Live';
    startButton.innerHTML = `<span class="record-dot"></span> ${buttonLabel}`;
    startButton.disabled = !result.configured || isBusy;
    stopButton.hidden = !result.start_requested;
    stopButton.disabled = result.state === 'stopping';
    actionHint.textContent = hint;
    previewMessage.textContent = message;
    if (result.video_mode === 'generated' && !loopPreviewVideo.dataset.loaded && !previewLoading) {
      loopPreviewVideo.dataset.format = selectedFormat;
      loadFreshPreview();
    } else if (result.video_mode === 'custom' && result.video_configured && !loopPreviewVideo.dataset.loaded) {
      loopPreviewVideo.dataset.loaded = 'true';
      loopPreviewVideo.dataset.format = selectedFormat;
      previewDesignId.textContent = 'CUSTOM VIDEO';
      previewDesignMessage.textContent = 'Custom stream video preview';
      loopPreviewVideo.src = '/api/loop-video';
      loopPreviewVideo.load();
    } else if (!result.video_configured && loopPreviewVideo.dataset.loaded) {
      delete loopPreviewVideo.dataset.loaded;
      loopPreviewVideo.removeAttribute('src');
      loopPreviewVideo.load();
      videoPreview.classList.remove('has-preview');
    }
  } catch (error) {
    if (!(error instanceof TypeError)) {
      throw error;
    }
    encoderStatus.textContent = 'Offline';
    encoderStatusDot.className = 'health-dot status-idle';
    encoderDetails.textContent = 'Streaming server unavailable';
    streamStatus.classList.remove('rehearsing');
    streamStatus.innerHTML = '<i></i> OFFLINE';
    startButton.innerHTML = '<span class="record-dot"></span> Start Live';
    startButton.disabled = true;
    stopButton.hidden = true;
    actionHint.textContent = 'Start the Python service or check the server connection.';
    previewMessage.textContent = 'Cannot reach the streaming server';
  }
}

for (const button of formatButtons) {
  button.addEventListener('click', async () => {
    if (formatSaving || button.classList.contains('active')) {
      return;
    }
    formatSaving = true;
    for (const option of formatButtons) {
      option.disabled = true;
    }
    formatHint.textContent = 'Updating stream format…';
    try {
      const response = await fetch('/api/stream-format', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ format: button.dataset.streamFormat }),
        cache: 'no-store'
      });
      const result = await response.json();
      if (!response.ok) {
        showToast(result.error || 'Could not change the stream format.');
        return;
      }
      updateFormatControls(result.format);
      showToast(`${formatDetails[result.format].label} ${formatDetails[result.format].aspect} format selected.`);
    } catch (error) {
      if (!(error instanceof TypeError)) {
        throw error;
      }
      showToast('Could not reach the streaming server.');
    } finally {
      formatSaving = false;
      await refreshStreamStatus();
    }
  });
}

const stopButton = document.querySelector('#stopButton');

startButton.addEventListener('click', async () => {
  startButton.disabled = true;
  try {
    const response = await fetch('/api/stream/start', { method: 'POST', cache: 'no-store' });
    const result = await response.json();
    if (!response.ok) {
      showToast(result.error || 'Could not start the live stream.');
      return;
    }
    showToast('Live stream is starting. Check the status above and YouTube Studio.');
  } catch (error) {
    if (!(error instanceof TypeError)) {
      throw error;
    }
    showToast('Could not reach the streaming server.');
  } finally {
    await refreshStreamStatus();
  }
});

stopButton.addEventListener('click', async () => {
  stopButton.disabled = true;
  try {
    const response = await fetch('/api/stream/stop', { method: 'POST', cache: 'no-store' });
    const result = await response.json();
    if (!response.ok) {
      showToast(result.error || 'Could not stop the live stream.');
      return;
    }
    showToast('Stream stopped. The loop will not reconnect until you press Start Live again.');
  } catch (error) {
    if (!(error instanceof TypeError)) {
      throw error;
    }
    showToast('Could not reach the streaming server.');
  } finally {
    await refreshStreamStatus();
  }
});

loopPreviewVideo.addEventListener('loadeddata', () => {
  videoPreview.classList.add('has-preview');
});
loopPreviewVideo.addEventListener('error', () => {
  delete loopPreviewVideo.dataset.loaded;
  delete loopPreviewVideo.dataset.designId;
  videoPreview.classList.remove('has-preview');
});

loopPreviewVideo.addEventListener('ended', () => {
  if (loopPreviewVideo.dataset.loaded && loopPreviewVideo.dataset.designId) {
    loadFreshPreview(true);
  }
});

refreshStreamStatus();
window.setInterval(refreshStreamStatus, 10000);

document.querySelector('#previewButton').addEventListener('click', (event) => {
  if (!loopPreviewVideo.src) {
    showToast('The generated loop preview is still being prepared.');
    return;
  }
  loopPreviewVideo.play().catch((error) => {
    if (!(error instanceof DOMException)) {
      throw error;
    }
    showToast('Use the video controls to start the preview.');
  });
});

document.querySelector('#copyLinkButton').addEventListener('click', async () => {
  const demoUrl = 'https://youtube.com/@asiftechglobal/live';
  try {
    await navigator.clipboard.writeText(demoUrl);
    showToast('Demo channel link copied.');
  } catch {
    showToast(`Demo channel link: ${demoUrl}`);
  }
});

document.querySelector('#scheduleButton').addEventListener('click', () => {
  showToast('Connect your YouTube account to schedule a stream.');
});

document.querySelector('#connectButton').addEventListener('click', () => {
  showToast('YouTube account connection is not configured in this demo.');
});

document.querySelector('#editTitleButton').addEventListener('click', () => {
  showToast('Connect your YouTube account to edit stream details.');
});

document.querySelector('#chatForm').addEventListener('submit', (event) => {
  event.preventDefault();
  showToast('Chat is a preview and is not connected to YouTube.');
});
