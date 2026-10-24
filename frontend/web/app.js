const picker = document.querySelector("#audio-file");
const dropzone = document.querySelector("#dropzone");
const selected = document.querySelector("#selected-file");
const analyzeButton = document.querySelector("#analyze");
const player = document.querySelector("#audio-player");
const emptyState = document.querySelector("#empty-state");
const resultContent = document.querySelector("#result-content");
const streamStartButton = document.querySelector("#stream-start");
const streamStopButton = document.querySelector("#stream-stop");
const streamStatus = document.querySelector("#stream-status");
const callStreamTestButton = document.querySelector("#call-stream-test");
const callStreamTestStatus = document.querySelector("#call-stream-test-status");
let selectedFile = null;
let playerUrl = null;
let microphoneStream = null;
let microphoneContext = null;
let microphoneProcessor = null;
let streamSocket = null;
let pendingMicrophoneFrames = [];
let pendingMicrophoneSamples = 0;
let activeRecorder = null;
let recorderStream = null;
let recordingChunks = [];
let recordingTimer = null;
const liveScoreHistory = [];
const liveWaveformSamples = [];
const MAX_LIVE_SCORE_POINTS = 40;
const MAX_LIVE_WAVEFORM_SAMPLES = 320;
const BACKEND_ORIGINS = [
  window.location.origin,
  ...(["localhost", "127.0.0.1"].includes(window.location.hostname) &&
  window.location.port !== "8000"
    ? [`${window.location.protocol}//${window.location.hostname}:8000`]
    : []),
];

function backendOriginsRemain(origin) {
  return BACKEND_ORIGINS.indexOf(origin) < BACKEND_ORIGINS.length - 1;
}

const HISTORY_KEY = "voiceshield-check-history";
const HISTORY_ENABLED_KEY = "voiceshield-save-history";
const MAX_HISTORY_ITEMS = 30;
const resultLabels = {
  "likely genuine": "Likely genuine",
  suspicious: "Suspicious",
  inconclusive: "Can’t tell",
};

function translateUi(text) {
  return window.VoiceShieldI18n?.t(text) || text;
}

function friendlyResult(label) {
  return translateUi(resultLabels[label] || "Can’t tell");
}

function readHistory() {
  try {
    const saved = localStorage.getItem(HISTORY_KEY);
    if (!saved) return [];
    const history = JSON.parse(saved);
    if (!Array.isArray(history)) throw new Error("Saved check history has an invalid format.");
    return history.filter(
      (item) =>
        item &&
        typeof item.id === "string" &&
        typeof item.label === "string" &&
        typeof item.createdAt === "string" &&
        typeof item.reason === "string",
    );
  } catch (error) {
    document.querySelector("#history-storage-message").textContent =
      `Could not read saved history: ${error.message}`;
    return [];
  }
}

function isHistoryEnabled() {
  try {
    return localStorage.getItem(HISTORY_ENABLED_KEY) !== "false";
  } catch {
    document.querySelector("#history-storage-message").textContent =
      "Browser storage is unavailable. Check results will not persist after this page closes.";
    return false;
  }
}

function renderHistory() {
  const history = readHistory();
  const list = document.querySelector("#history-list");
  const dashboard = document.querySelector("#dashboard-alerts");
  list.replaceChildren();
  dashboard.replaceChildren();
  const historyEnabled = isHistoryEnabled();
  document.querySelector("#history-setting").checked = historyEnabled;
  document.querySelector("#clear-history").disabled = history.length === 0;

  if (!historyEnabled) {
    const message = document.createElement("p");
    message.className = "empty-copy";
    message.textContent = "History saving is off. New results will not be kept in this browser.";
    list.append(message);
    dashboard.append(message.cloneNode(true));
    return;
  }
  if (history.length === 0) {
    const message = document.createElement("p");
    message.className = "empty-copy";
    message.textContent = "No checks saved yet. Results will appear here after you check a recording.";
    list.append(message);
    dashboard.append(message.cloneNode(true));
    return;
  }

  for (const item of history) {
    list.append(createHistoryCard(item, false));
  }
  for (const item of history.slice(0, 3)) {
    dashboard.append(createHistoryCard(item, true));
  }
}

function createHistoryCard(item, compact) {
  const card = document.createElement("article");
  card.className = `history-card${compact ? " history-card-compact" : ""}`;
  const indicator = document.createElement("span");
  indicator.className = "history-indicator";
  indicator.dataset.kind = item.label;
  indicator.setAttribute("aria-hidden", "true");
  const body = document.createElement("div");
  body.className = "history-card-body";
  const title = document.createElement("strong");
  title.textContent = friendlyResult(item.label);
  const date = document.createElement("time");
  date.dateTime = item.createdAt;
  const created = new Date(item.createdAt);
  date.textContent = Number.isNaN(created.getTime())
    ? "Date unavailable"
    : new Intl.DateTimeFormat(window.VoiceShieldI18n?.locale || undefined, { dateStyle: "medium", timeStyle: "short" }).format(created);
  const reason = document.createElement("p");
  reason.textContent = item.reason;
  body.append(title, date, reason);
  card.append(indicator, body);
  return card;
}

function saveHistoryEntry(result) {
  if (!isHistoryEnabled()) {
    renderHistory();
    return;
  }
  const entry = {
    id: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`,
    createdAt: new Date().toISOString(),
    label: result.label,
    reason: result.reason,
    duration: result.clip_duration_s,
  };
  try {
    const history = readHistory();
    localStorage.setItem(HISTORY_KEY, JSON.stringify([entry, ...history].slice(0, MAX_HISTORY_ITEMS)));
    document.querySelector("#history-storage-message").textContent =
      "Results are saved in this browser only. Audio is not included.";
  } catch (error) {
    document.querySelector("#history-storage-message").textContent =
      `Could not save this result in browser history: ${error.message}`;
  }
  renderHistory();
}

function showScreeningSignal(label, score, reason) {
  emptyState.hidden = true;
  resultContent.hidden = false;
  const resultLabel = document.querySelector("#result-label");
  resultLabel.textContent = friendlyResult(label);
  resultLabel.dataset.kind = label;
  document.querySelector("#result-reason").textContent = reason;
  const riskGraph = document.querySelector("#risk-graph");
  const riskScale = document.querySelector("#risk-scale");
  riskGraph.hidden = score === null;
  if (score !== null) {
    riskScale.dataset.signal = label;
    riskScale.setAttribute(
      "aria-label",
      `Model screening signal: ${label}. This is not a probability.`,
    );
  } else {
    riskScale.removeAttribute("data-signal");
  }
  document.querySelector("#score-row").hidden = score === null;
  if (score !== null) document.querySelector("#score").textContent = score.toFixed(4);
  document.querySelector("#risk-percentage").textContent = "Not available";
  document.querySelector("#risk-percentage-note").textContent =
    "A percentage is not shown because this model’s raw score is not calibrated as a probability.";
  document.querySelector("#high-risk-alert").hidden = label !== "suspicious";
}

function updateStreamStatus(message, state = "idle") {
  streamStatus.textContent = message;
  streamStatus.dataset.state = state;
}

function resetLiveScoreGraph() {
  liveScoreHistory.length = 0;
  liveWaveformSamples.length = 0;
  document.querySelector("#live-score-chart").hidden = false;
  document.querySelector("#live-score-chart").dataset.streaming = "true";
  document.querySelector("#live-score-line").setAttribute("points", "");
  document.querySelector("#live-score-dot").setAttribute("cx", "0");
  document.querySelector("#live-waveform-line").setAttribute("d", "");
  document.querySelector("#live-score-state").textContent = "MIC STARTING";
  document.querySelector("#live-score-value").textContent = "Waiting for the first score";
  document.querySelector("#live-score-plot").setAttribute(
    "aria-label",
    "Live raw model score trend. Waiting for the first score.",
  );
  document.querySelector("#live-waveform").setAttribute(
    "aria-label",
    "Live microphone waveform. Waiting for microphone audio.",
  );
}

function updateLiveWaveform(samples) {
  const stride = Math.max(1, Math.floor(samples.length / 48));
  for (let index = 0; index < samples.length; index += stride) {
    liveWaveformSamples.push(samples[index]);
  }
  if (liveWaveformSamples.length > MAX_LIVE_WAVEFORM_SAMPLES) {
    liveWaveformSamples.splice(0, liveWaveformSamples.length - MAX_LIVE_WAVEFORM_SAMPLES);
  }

  const peak = Math.max(0.015, ...liveWaveformSamples.map((sample) => Math.abs(sample)));
  const path = liveWaveformSamples.map((sample, index) => {
    const x = liveWaveformSamples.length === 1
      ? 160
      : (index / (liveWaveformSamples.length - 1)) * 320;
    const y = 36 - (sample / peak) * 30;
    return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");

  document.querySelector("#live-waveform-line").setAttribute("d", path);
  document.querySelector("#live-waveform").setAttribute(
    "aria-label",
    `Live microphone waveform is active, showing ${liveWaveformSamples.length} recent auto-scaled audio samples.`,
  );
}

function updateLiveScoreGraph(score, label) {
  liveScoreHistory.push(score);
  if (liveScoreHistory.length > MAX_LIVE_SCORE_POINTS) liveScoreHistory.shift();

  const scale = Math.max(1, ...liveScoreHistory.map((value) => Math.abs(value))) * 1.1;
  const points = liveScoreHistory.map((value, index) => {
    const x = liveScoreHistory.length === 1
      ? 160
      : (index / (liveScoreHistory.length - 1)) * 320;
    const y = 60 - (value / scale) * 52;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const [lastX, lastY] = points[points.length - 1].split(",");
  const valueText = `Latest raw score: ${score.toFixed(4)} · ${friendlyResult(label)}`;

  document.querySelector("#live-score-line").setAttribute("points", points.join(" "));
  document.querySelector("#live-score-dot").setAttribute("cx", lastX);
  document.querySelector("#live-score-dot").setAttribute("cy", lastY);
  document.querySelector("#live-score-value").textContent = valueText;
  document.querySelector("#live-score-plot").setAttribute(
    "aria-label",
    `${valueText}. Raw score trend from the last ${liveScoreHistory.length} microphone updates; not a probability.`,
  );
}

async function loadProtectionStatus() {
  const availability = document.querySelector("#protection-availability");
  const details = document.querySelector("#protection-details");
  const chip = document.querySelector("#protection-chip");
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Detection service is unavailable.");
    setPrivateReportAvailability(data.private_reports_enabled === true);
    availability.textContent =
      data.status !== "ready" ? "Demo only" : data.screening_labels_validated ? "Ready" : "Score only";
    details.textContent = data.model_id
      ? data.screening_labels_validated
        ? `${translateUi("Local screening model connected")} · ${data.model_id}`
        : `${translateUi("Model connected")} · ${data.model_id} · ${translateUi("labels not validated for your recordings")}`
      : translateUi(data.message);
    chip.textContent =
      data.status !== "ready" ? "DEMO" : data.screening_labels_validated ? "READY" : "UNVALIDATED";
    chip.classList.toggle(
      "status-demo",
      data.status !== "ready" || !data.screening_labels_validated,
    );
    chip.classList.toggle("status-ready", data.status === "ready" && data.screening_labels_validated);
  } catch {
    setPrivateReportAvailability(false, "Report intake availability could not be confirmed. Private report intake is disabled for this session.");
    availability.textContent = "Not connected";
    details.textContent = "The local service could not be reached. Try again when it is running.";
    chip.textContent = "OFFLINE";
    chip.classList.remove("status-ready", "status-demo");
    chip.classList.add("status-offline");
  }
}

function setPrivateReportAvailability(enabled, message) {
  document.querySelector("#report-form-panel").hidden = !enabled;
  document.querySelector("#report-history-panel").hidden = !enabled;
  document.querySelector("#report-privacy-notice").hidden = !enabled;
  const notice = document.querySelector("#report-disabled-notice");
  notice.hidden = enabled;
  if (!enabled && message) {
    notice.querySelector("h2").textContent = "Private report intake unavailable";
    notice.querySelector("p").textContent = message;
  } else if (!enabled) {
    notice.querySelector("h2").textContent = "Private report intake is disabled";
    notice.querySelector("p").textContent =
      "This public deployment does not store incident reports or evidence. Use the official reporting service below to report an incident.";
  }
}

function showScreen(screenId) {
  for (const view of document.querySelectorAll(".app-view")) {
    view.hidden = view.id !== screenId;
    view.classList.toggle("is-entering", view.id === screenId);
  }
  for (const button of document.querySelectorAll("[data-screen]")) {
    const active = button.dataset.screen === screenId;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  if (screenId === "history-view" || screenId === "home-view" || screenId === "settings-view") {
    renderHistory();
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function stopRecording() {
  if (recordingTimer) {
    clearTimeout(recordingTimer);
    recordingTimer = null;
  }
  if (activeRecorder && activeRecorder.state !== "inactive") {
    activeRecorder.stop();
  }
}

async function startRecording() {
  const startButton = document.querySelector("#record-start");
  const stopButton = document.querySelector("#record-stop");
  const status = document.querySelector("#record-status");
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    status.textContent = "Recording is not supported in this browser. Upload a WAV, FLAC, or OGG clip instead.";
    return;
  }
  startButton.disabled = true;
  status.textContent = "Requesting microphone permission…";
  try {
    recorderStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const preferredType = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm"]
      .find((type) => MediaRecorder.isTypeSupported(type));
    activeRecorder = new MediaRecorder(recorderStream, preferredType ? { mimeType: preferredType } : {});
    recordingChunks = [];
    activeRecorder.addEventListener("dataavailable", (event) => {
      if (event.data.size > 0) recordingChunks.push(event.data);
    });
    activeRecorder.addEventListener("stop", () => {
      const blob = new Blob(recordingChunks, { type: activeRecorder?.mimeType || "audio/webm" });
      const extension = blob.type.includes("ogg") ? "ogg" : "webm";
      chooseFile(new File([blob], `voice-check-recording.${extension}`, { type: blob.type }));
      status.textContent = "Recording ready. Play it back, then choose Check recording.";
      startButton.disabled = false;
      startButton.hidden = false;
      stopButton.hidden = true;
      for (const track of recorderStream?.getTracks() || []) track.stop();
      recorderStream = null;
      activeRecorder = null;
    }, { once: true });
    activeRecorder.start();
    startButton.hidden = true;
    stopButton.hidden = false;
    status.textContent = "Recording… Speak normally, then stop. Maximum length: 10 seconds.";
    recordingTimer = setTimeout(stopRecording, 10_000);
  } catch (error) {
    for (const track of recorderStream?.getTracks() || []) track.stop();
    recorderStream = null;
    startButton.disabled = false;
    status.textContent = `Could not record: ${error.message || "microphone permission was not granted."}`;
  }
}

async function stopMicrophoneDemo(message = "Live mic demo stopped. No call audio was captured.") {
  document.querySelector("#live-score-chart").dataset.streaming = "false";
  document.querySelector("#live-score-state").textContent = "IDLE";
  if (microphoneProcessor) {
    microphoneProcessor.disconnect();
    microphoneProcessor.onaudioprocess = null;
    microphoneProcessor = null;
  }
  if (microphoneStream) {
    for (const track of microphoneStream.getTracks()) track.stop();
    microphoneStream = null;
  }
  if (microphoneContext && microphoneContext.state !== "closed") {
    await microphoneContext.close();
  }
  microphoneContext = null;
  if (streamSocket && streamSocket.readyState < WebSocket.CLOSING) {
    streamSocket.close(1000, "Microphone demo stopped");
  }
  streamSocket = null;
  pendingMicrophoneFrames = [];
  pendingMicrophoneSamples = 0;
  streamStartButton.hidden = false;
  streamStopButton.hidden = true;
  updateStreamStatus(message);
}

async function startMicrophoneDemo() {
  streamStartButton.disabled = true;
  resetLiveScoreGraph();
  updateStreamStatus("Requesting microphone permission…", "working");
  try {
    microphoneStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false },
    });
    microphoneContext = new AudioContext({ sampleRate: 16_000 });
    await microphoneContext.resume();
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    streamSocket = new WebSocket(
      `${protocol}//${window.location.host}/api/stream?sample_rate=${microphoneContext.sampleRate}`,
    );
    streamSocket.binaryType = "arraybuffer";
    streamSocket.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        void stopMicrophoneDemo("The streaming service returned an invalid response.");
        return;
      }
      if (data.error) {
        void stopMicrophoneDemo(data.error);
      } else if (data.status === "ready") {
        updateStreamStatus(
          `Mic active. Waiting for ${data.window_duration_s.toFixed(2)} seconds of speech before the first score; then updating every 500 ms. Not a phone-call feed.`,
          "active",
        );
      } else if (data.status === "warming_up") {
        updateStreamStatus(
          `Mic active · ${Math.min(data.elapsed_s, data.required_s).toFixed(1)} / ${data.required_s.toFixed(2)} s buffered before first score.`,
          "active",
        );
      } else if (data.status === "scored") {
        showScreeningSignal(data.label, data.score_raw, data.reason);
        document.querySelector("#result-reason").textContent = data.reason;
        updateLiveScoreGraph(data.score_raw, data.label);
        document.querySelector("#model").textContent = data.model_id;
        document.querySelector("#duration").textContent = `${data.elapsed_s.toFixed(2)} s live`;
        updateStreamStatus(
          `${data.elapsed_s.toFixed(1)} s captured · ${data.label} · score refresh target: 500 ms. Microphone only, not call audio.`,
          "active",
        );
      }
    };
    streamSocket.onerror = () => {
      void stopMicrophoneDemo("Could not connect to the local streaming service.");
    };
    streamSocket.onclose = () => {
      if (microphoneStream) {
        void stopMicrophoneDemo("Streaming connection closed.");
      }
    };
    await new Promise((resolve, reject) => {
      streamSocket.addEventListener("open", resolve, { once: true });
      streamSocket.addEventListener("error", () => reject(new Error("Could not open the streaming connection.")), {
        once: true,
      });
    });
    document.querySelector("#live-score-state").textContent = "LIVE MIC";

    const source = microphoneContext.createMediaStreamSource(microphoneStream);
    microphoneProcessor = microphoneContext.createScriptProcessor(4096, 1, 1);
    microphoneProcessor.onaudioprocess = (event) => {
      if (!streamSocket || streamSocket.readyState !== WebSocket.OPEN) return;
      const input = event.inputBuffer.getChannelData(0);
      updateLiveWaveform(input);
      pendingMicrophoneFrames.push(new Float32Array(input));
      pendingMicrophoneSamples += input.length;
      const packetSamples = Math.floor(microphoneContext.sampleRate / 2);
      while (pendingMicrophoneSamples >= packetSamples) {
        const combined = new Float32Array(pendingMicrophoneSamples);
        let offset = 0;
        for (const frame of pendingMicrophoneFrames) {
          combined.set(frame, offset);
          offset += frame.length;
        }
        const packet = combined.subarray(0, packetSamples);
        const pcm = new Int16Array(packet.length);
        for (let index = 0; index < packet.length; index += 1) {
          const value = Math.max(-1, Math.min(1, packet[index]));
          pcm[index] = value < 0 ? Math.round(value * 32768) : Math.round(value * 32767);
        }
        streamSocket.send(pcm.buffer);
        const remainder = combined.subarray(packetSamples);
        pendingMicrophoneFrames = remainder.length ? [remainder.slice()] : [];
        pendingMicrophoneSamples = remainder.length;
      }
      for (let channel = 0; channel < event.outputBuffer.numberOfChannels; channel += 1) {
        event.outputBuffer.getChannelData(channel).fill(0);
      }
    };
    source.connect(microphoneProcessor);
    microphoneProcessor.connect(microphoneContext.destination);
    streamStartButton.hidden = true;
    streamStopButton.hidden = false;
    updateStreamStatus("Microphone active. Speak a short phrase; first result appears after the model window fills.", "active");
  } catch (error) {
    await stopMicrophoneDemo(error.message || "Could not start the microphone demo.");
  } finally {
    streamStartButton.disabled = false;
  }
}

function chooseFile(file) {
  if (!file) return;
  selectedFile = file;
  document.querySelector("#file-name").textContent = file.name;
  document.querySelector("#file-size").textContent = `${(file.size / 1024 / 1024).toFixed(2)} MB`;
  selected.hidden = false;
  dropzone.hidden = true;
  analyzeButton.disabled = false;
  if (playerUrl) URL.revokeObjectURL(playerUrl);
  playerUrl = URL.createObjectURL(file);
  player.src = playerUrl;
  player.hidden = false;
  clearResult();
}

function clearResult() {
  emptyState.hidden = false;
  emptyState.querySelector("p").textContent = "Your result will appear here";
  emptyState.querySelector(".empty-state > span").textContent = "Choose a clip to begin analysis";
  resultContent.hidden = true;
  document.querySelector("#high-risk-alert").hidden = true;
  document.querySelector("#warning-box").hidden = true;
  document.querySelector("#clip-length").textContent = "Duration shown after audio loads";
  document.querySelector("#status-dot").classList.remove("ready");
}

async function loadDemoExamples() {
  try {
    const response = await fetch("/api/examples");
    if (!response.ok) throw new Error("Could not check local examples.");
    const examples = await response.json();
    for (const button of document.querySelectorAll(".demo-button")) {
      const example = examples.find((item) => item.id === button.dataset.example);
      button.disabled = !example;
      if (example) button.textContent = `${example.label} example`;
    }
    if (examples.length === 2) {
      document.querySelector("#demo-note").textContent =
        "Team-provided permitted clips. The result still depends on the configured model.";
    }
  } catch (error) {
    document.querySelector("#demo-note").textContent = error.message;
  }
}

async function loadPerformance() {
  const message = document.querySelector("#performance-message");
  const table = document.querySelector("#performance-table");
  try {
    const response = await fetch("/api/performance");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not load evaluation report.");
    message.textContent = data.message;
    if (data.phone_stress_description) {
      message.textContent += ` ${data.phone_stress_description}`;
    }
    document.querySelector("#download-report").hidden = !data.available;
    const modelInfo = document.querySelector("#performance-model");
    modelInfo.hidden = !data.available;
    if (data.available) {
      modelInfo.textContent = `Model: ${data.model || "Not recorded"} · Fixed decision threshold: ${data.threshold}`;
    }
    const rows = document.querySelector("#performance-rows");
    rows.replaceChildren();
    for (const report of data.reports) {
      const row = document.createElement("tr");
      const values = [
        report.source,
        report.partition,
        report.condition || "original",
        report.samples,
        Number(report.balanced_accuracy).toFixed(3),
        Number(report.eer).toFixed(3),
        report.false_positives,
        report.false_negatives,
      ];
      for (const value of values) {
        const cell = document.createElement("td");
        cell.textContent = String(value);
        row.append(cell);
      }
      rows.append(row);
    }
    table.hidden = data.reports.length === 0;
  } catch (error) {
    table.hidden = true;
    document.querySelector("#download-report").hidden = true;
    document.querySelector("#performance-model").hidden = true;
    message.textContent = error.message;
  }
}

picker.addEventListener("change", () => chooseFile(picker.files[0]));
streamStartButton.addEventListener("click", startMicrophoneDemo);
streamStopButton.addEventListener("click", () => void stopMicrophoneDemo());
callStreamTestButton.addEventListener("click", async () => {
  callStreamTestButton.disabled = true;
  callStreamTestStatus.textContent = "Connecting to backend and running simulated stream…";
  callStreamTestStatus.dataset.kind = "pending";
  try {
    let result = null;
    let lastFailure = null;
    for (const backendOrigin of BACKEND_ORIGINS) {
      try {
        const response = await fetch(`${backendOrigin}/api/call-stream/demo-test`, {
          method: "POST",
          headers: { Accept: "application/json" },
        });
        if (response.status === 404 && backendOriginsRemain(backendOrigin)) {
          lastFailure = new Error("No stream-test endpoint on this page's server.");
          continue;
        }
        let body;
        try {
          body = await response.json();
        } catch {
          if (backendOriginsRemain(backendOrigin)) {
            lastFailure = new Error("No JSON test response from this page's server.");
            continue;
          }
          throw new Error(`Backend returned an unreadable response (HTTP ${response.status}).`);
        }
        if (!response.ok) {
          if (response.status === 404) {
            throw new Error(
              "This backend does not have the stream-test endpoint yet. Close the old VoiceShield server, restart it with start.bat, and reload the page."
            );
          }
          throw new Error(body.detail || `Backend test failed (HTTP ${response.status}).`);
        }
        result = body;
        break;
      } catch (error) {
        if (error instanceof TypeError && backendOriginsRemain(backendOrigin)) {
          lastFailure = error;
          continue;
        }
        throw error;
      }
    }
    if (result === null) throw lastFailure || new Error("No backend returned a test result.");
    callStreamTestStatus.textContent =
      `Backend test passed · ${result.result}. ${result.steps.join(" · ")}. ${result.notice}`;
    callStreamTestStatus.dataset.kind = "passed";
  } catch (error) {
    callStreamTestStatus.textContent =
      error instanceof TypeError
        ? "Could not contact the backend at http://127.0.0.1:8000. Start or restart VoiceShield with start.bat, then reload this page. A separate preview must allow requests to the local backend."
        : `Backend test failed: ${error.message}`;
    callStreamTestStatus.dataset.kind = "failed";
  } finally {
    callStreamTestButton.disabled = false;
  }
});
document.querySelector("#record-start").addEventListener("click", () => void startRecording());
document.querySelector("#record-stop").addEventListener("click", stopRecording);
for (const button of document.querySelectorAll("[data-screen]")) {
  button.addEventListener("click", () => showScreen(button.dataset.screen));
}
for (const button of document.querySelectorAll("[data-open-screen]")) {
  button.addEventListener("click", () => showScreen(button.dataset.openScreen));
}
document.querySelector("#clear-history").addEventListener("click", () => {
  try {
    localStorage.removeItem(HISTORY_KEY);
    document.querySelector("#history-storage-message").textContent = "Saved check history was cleared from this browser.";
    renderHistory();
  } catch (error) {
    document.querySelector("#history-storage-message").textContent =
      `Could not clear browser history: ${error.message}`;
  }
});
document.querySelector("#history-setting").addEventListener("change", (event) => {
  try {
    localStorage.setItem(HISTORY_ENABLED_KEY, String(event.target.checked));
    if (!event.target.checked) localStorage.removeItem(HISTORY_KEY);
    document.querySelector("#history-storage-message").textContent = event.target.checked
      ? "New result labels and dates will be saved in this browser only."
      : "History saving is off; saved check history was cleared from this browser.";
    renderHistory();
  } catch (error) {
    event.target.checked = !event.target.checked;
    document.querySelector("#history-storage-message").textContent =
      `Could not update this setting: ${error.message}`;
  }
});

const cyberReportForm = document.querySelector("#cyber-report-form");
const reportCountrySelect = document.querySelector("#report-country");
const reportCountryValue = document.querySelector("#report-country-value");
const reportEvidenceInput = document.querySelector("#report-evidence");
const reportStatus = document.querySelector("#cyber-report-status");
const reportSubmitButton = document.querySelector("#submit-cyber-report");
const reportHistoryForm = document.querySelector("#report-history-form");
const reportHistoryStatus = document.querySelector("#report-history-status");
const privateReportResult = document.querySelector("#private-report-result");

function populateReportSteps(list, steps) {
  list.replaceChildren();
  steps.forEach((step) => {
    const item = document.createElement("li");
    item.textContent = step;
    list.append(item);
  });
}

async function updateOfficialReportingService() {
  const guidance = document.querySelector("#official-report-guidance");
  const link = document.querySelector("#official-report-link");
  guidance.textContent = "Loading the official reporting service…";
  link.hidden = true;
  try {
    const response = await fetch("/api/cyber-fraud/reporting-services", { cache: "no-store" });
    const services = await response.json();
    if (!response.ok) throw new Error(services.detail || "Could not load official reporting information.");
    const service = services.find((item) => item.code === reportCountrySelect.value);
    if (!service) throw new Error("No verified reporting service is available for this country.");
    guidance.textContent = `${service.name}: ${service.guidance}`;
    link.href = service.url;
    link.textContent = `Open ${service.name} ↗`;
    link.hidden = false;
  } catch (error) {
    guidance.textContent = `${error.message} You can still save a private in-app report, but the official service details are unavailable right now.`;
  }
}

function updateReportContactRequirements() {
  const email = document.querySelector("#contact-email");
  const phone = document.querySelector("#contact-phone");
  const hasCallbackPhone = callbackRequestedInput.checked && callbackPhoneInput.value.trim();
  email.required = !phone.value.trim() && !hasCallbackPhone;
  phone.required = !email.value.trim() && !hasCallbackPhone;
}

const callbackRequestedInput = document.querySelector("#callback-requested");
const callbackFields = document.querySelector("#callback-fields");
const callbackPhoneInput = document.querySelector("#callback-phone");
const callbackTimeInput = document.querySelector("#callback-preferred-time");
const callbackTimezoneOffsetInput = document.querySelector("#callback-timezone-offset");
const callbackConsentInput = document.querySelector("#callback-consent");

function updateCallbackTimezoneOffset() {
  if (callbackTimeInput.value) {
    callbackTimezoneOffsetInput.value = String(-new Date(callbackTimeInput.value).getTimezoneOffset());
  }
}

function updateCallbackRequirements() {
  const requested = callbackRequestedInput.checked;
  callbackFields.hidden = !requested;
  callbackPhoneInput.required = requested;
  callbackTimeInput.required = requested;
  callbackConsentInput.required = requested;
  callbackPhoneInput.disabled = !requested;
  callbackTimeInput.disabled = !requested;
  callbackConsentInput.disabled = !requested;
  callbackTimezoneOffsetInput.disabled = !requested;
  if (requested) {
    const now = new Date(Date.now() + 60_000);
    const localDateTime = new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
      .toISOString()
      .slice(0, 16);
    callbackTimeInput.min = localDateTime;
    updateCallbackTimezoneOffset();
  } else {
    callbackTimezoneOffsetInput.value = "";
  }
  updateReportContactRequirements();
}

reportCountrySelect.addEventListener("change", updateOfficialReportingService);
reportCountrySelect.addEventListener("change", () => {
  reportCountryValue.value = reportCountrySelect.value;
});
document.querySelector("#contact-email").addEventListener("input", updateReportContactRequirements);
document.querySelector("#contact-phone").addEventListener("input", updateReportContactRequirements);
callbackRequestedInput.addEventListener("change", updateCallbackRequirements);
callbackPhoneInput.addEventListener("input", updateReportContactRequirements);
callbackTimeInput.addEventListener("input", updateCallbackTimezoneOffset);
callbackTimeInput.addEventListener("change", updateCallbackTimezoneOffset);
updateCallbackRequirements();
reportEvidenceInput.addEventListener("change", () => {
  const selectedEvidence = reportEvidenceInput.files[0];
  const label = document.querySelector("#evidence-file-name");
  if (!selectedEvidence) {
    label.textContent = "No evidence file selected.";
    return;
  }
  if (selectedEvidence.size > 5 * 1024 * 1024) {
    reportEvidenceInput.value = "";
    label.textContent = "That file exceeds the 5 MB limit. Choose a smaller evidence file.";
    return;
  }
  label.textContent = `${selectedEvidence.name} · ${(selectedEvidence.size / 1024 / 1024).toFixed(2)} MB`;
});

cyberReportForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  updateReportContactRequirements();
  if (!cyberReportForm.reportValidity()) {
    reportStatus.textContent = "Check the required fields and correct any highlighted values.";
    return;
  }

  reportSubmitButton.disabled = true;
  reportStatus.textContent = "Saving privately on this computer…";
  document.querySelector("#cyber-report-success").hidden = true;
  try {
    const response = await fetch("/api/cyber-fraud/reports", {
      method: "POST",
      body: new FormData(cyberReportForm),
    });
    const result = await response.json();
    if (!response.ok) {
      const detail = Array.isArray(result.detail)
        ? result.detail
            .map((item) => (typeof item === "string" ? item : item.msg || "One or more fields are invalid."))
            .join(" ")
        : result.detail || "The report could not be saved.";
      throw new Error(detail);
    }
    document.querySelector("#cyber-report-success-message").textContent = result.message;
    document.querySelector("#cyber-report-reference").textContent = result.reference;
    document.querySelector("#cyber-report-submitted-status").textContent = result.status;
    document.querySelector("#cyber-report-access-key").textContent = result.access_key;
    document.querySelector("#copy-report-access-key").textContent = "Copy private access key";
    document.querySelector("#cyber-report-callback-status").textContent = result.callback_message;
    populateReportSteps(document.querySelector("#cyber-report-next-steps"), result.next_steps);
    document.querySelector("#cyber-report-success").hidden = false;
    reportStatus.textContent = result.callback_request_saved
      ? "Report and callback request saved. No call was placed; a callback is not guaranteed."
      : "Report saved. No callback was requested.";
    cyberReportForm.reset();
    reportCountryValue.value = reportCountrySelect.value;
    reportEvidenceInput.value = "";
    document.querySelector("#evidence-file-name").textContent = "No evidence file selected.";
    updateReportContactRequirements();
    updateCallbackRequirements();
    await updateOfficialReportingService();
    document.querySelector("#cyber-report-success").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    reportStatus.textContent = `Could not submit the in-app report: ${error.message}`;
  } finally {
    reportSubmitButton.disabled = false;
  }
});

document.querySelector("#copy-report-access-key").addEventListener("click", async () => {
  const key = document.querySelector("#cyber-report-access-key").textContent;
  try {
    await navigator.clipboard.writeText(key);
    document.querySelector("#copy-report-access-key").textContent = "Copied";
  } catch {
    reportStatus.textContent = "Clipboard access is unavailable. Select and copy the private access key manually.";
  }
});

reportHistoryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!reportHistoryForm.reportValidity()) return;
  reportHistoryStatus.textContent = "Looking up your private report…";
  privateReportResult.hidden = true;
  const data = new FormData(reportHistoryForm);
  try {
    const response = await fetch("/api/cyber-fraud/reports/history", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reference: data.get("reference"),
        access_key: data.get("access_key"),
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not find the report.");
    document.querySelector("#private-report-reference").textContent = result.reference;
    document.querySelector("#private-report-status").textContent = result.status;
    document.querySelector("#private-report-submitted").textContent =
      `Submitted: ${new Date(result.submitted_at).toLocaleString()}`;
    document.querySelector("#private-report-summary").textContent =
      `${result.incident_type.replaceAll("_", " ")} · ${result.incident_datetime} · ${result.description}`;
    document.querySelector("#private-report-amount").textContent = result.amount_lost === null
      ? "No loss amount was recorded."
      : `Reported loss: ${result.amount_lost} ${result.currency}`;
    document.querySelector("#private-report-evidence").textContent = result.evidence_attached
      ? "Evidence file is attached to the private report; it is not available for download here."
      : "No evidence file was attached.";
    document.querySelector("#private-report-callback").textContent = result.callback_requested
      ? `Callback request saved for ${result.callback_preferred_time || "the preferred time on file"}. No call was placed and a callback is not guaranteed.`
      : "No callback was requested.";
    populateReportSteps(document.querySelector("#private-report-next-steps"), result.next_steps);
    privateReportResult.hidden = false;
    reportHistoryStatus.textContent = "Private report found.";
  } catch (error) {
    reportHistoryStatus.textContent = `Could not retrieve report: ${error.message}`;
  }
});

document.querySelector("#audio-player").addEventListener("loadedmetadata", () => {
  if (Number.isFinite(player.duration)) {
    document.querySelector("#clip-length").textContent = `${player.duration.toFixed(2)} seconds`;
  }
});
window.addEventListener("beforeunload", () => {
  if (streamSocket && streamSocket.readyState < WebSocket.CLOSING) streamSocket.close();
  if (microphoneStream) {
    for (const track of microphoneStream.getTracks()) track.stop();
  }
  stopRecording();
});
document.querySelector("#remove-file").addEventListener("click", () => {
  selectedFile = null;
  picker.value = "";
  selected.hidden = true;
  dropzone.hidden = false;
  player.pause();
  player.removeAttribute("src");
  player.load();
  player.hidden = true;
  if (playerUrl) URL.revokeObjectURL(playerUrl);
  playerUrl = null;
  analyzeButton.disabled = true;
  clearResult();
});

for (const eventName of ["dragenter", "dragover"]) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add("dragging");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragging");
  });
}
dropzone.addEventListener("drop", (event) => chooseFile(event.dataTransfer.files[0]));

for (const button of document.querySelectorAll(".demo-button")) {
  button.addEventListener("click", async () => {
    const previousText = button.textContent;
    button.disabled = true;
    button.textContent = "Loading…";
    try {
      const response = await fetch(`/api/examples/${button.dataset.example}`);
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Could not load demo clip.");
      }
      const blob = await response.blob();
      chooseFile(new File([blob], `${button.dataset.example}.wav`, { type: "audio/wav" }));
    } catch (error) {
      document.querySelector("#demo-note").textContent = error.message;
    } finally {
      button.disabled = false;
      button.textContent = previousText;
    }
  });
}

document.querySelector("#refresh-performance").addEventListener("click", loadPerformance);

analyzeButton.addEventListener("click", async () => {
  if (!selectedFile) return;
  analyzeButton.disabled = true;
  document.querySelector("#button-label").textContent = "Analyzing locally…";
  document.querySelector("#status-dot").classList.add("ready");
  const body = new FormData();
  body.append("file", selectedFile);
  try {
    const response = await fetch("/api/analyze", { method: "POST", body });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Analysis failed.");
    emptyState.hidden = true;
    resultContent.hidden = false;
    const label = document.querySelector("#result-label");
    label.textContent = friendlyResult(result.label);
    label.dataset.kind = result.label;
    document.querySelector("#result-reason").textContent = result.reason;
    const riskPercentage =
      typeof result.risk_percentage === "number" && Number.isFinite(result.risk_percentage)
        ? result.risk_percentage
        : null;
    document.querySelector("#risk-percentage").textContent =
      riskPercentage === null ? "Not available" : `${riskPercentage.toFixed(1)}%`;
    document.querySelector("#risk-percentage-note").textContent =
      result.risk_percentage_reason ||
      "A percentage is only available when it has been calibrated and validated.";
    const riskGraph = document.querySelector("#risk-graph");
    const riskScale = document.querySelector("#risk-scale");
    riskGraph.hidden = result.score_raw === null;
    if (result.score_raw !== null) {
      riskScale.dataset.signal = result.label;
      riskScale.setAttribute(
        "aria-label",
        `Model screening signal: ${result.label}. This is not a probability.`,
      );
    } else {
      riskScale.removeAttribute("data-signal");
    }
    document.querySelector("#duration").textContent = `${result.clip_duration_s.toFixed(2)} s`;
    document.querySelector("#model").textContent = result.model_id;
    document.querySelector("#score-row").hidden = result.score_raw === null;
    if (result.score_raw !== null) document.querySelector("#score").textContent = result.score_raw.toFixed(4);
    document.querySelector("#limitation").textContent = result.limitation;
    document.querySelector("#high-risk-alert").hidden = result.label !== "suspicious";
    const warningBox = document.querySelector("#warning-box");
    const warningList = document.querySelector("#warnings");
    warningList.replaceChildren();
    for (const warning of result.warnings) {
      const item = document.createElement("li");
      item.textContent = warning;
      warningList.append(item);
    }
    warningBox.hidden = result.warnings.length === 0;
    saveHistoryEntry(result);
  } catch (error) {
    emptyState.hidden = true;
    resultContent.hidden = false;
    document.querySelector("#result-label").textContent = "Can’t tell";
    document.querySelector("#result-label").dataset.kind = "inconclusive";
    document.querySelector("#result-reason").textContent =
      `${error.message} This recording was not analyzed; try a supported 2–10 second clip.`;
    document.querySelector("#risk-percentage").textContent = "Not available";
    document.querySelector("#risk-percentage-note").textContent =
      "No risk percentage is available because this recording was not analyzed.";
    document.querySelector("#risk-graph").hidden = true;
    document.querySelector("#score-row").hidden = true;
    document.querySelector("#duration").textContent = "Not available";
    document.querySelector("#model").textContent = "Not analyzed";
    document.querySelector("#high-risk-alert").hidden = true;
    document.querySelector("#limitation").textContent =
      "A failed or unsupported clip is not evidence either way. Try another recording or verify the request through a trusted contact.";
    saveHistoryEntry({
      label: "inconclusive",
      reason: `Clip could not be analyzed: ${error.message}`,
      clip_duration_s: null,
    });
  } finally {
    analyzeButton.disabled = !selectedFile;
    document.querySelector("#button-label").textContent = "Analyze audio";
  }
});

loadDemoExamples();
loadPerformance();
loadProtectionStatus();
renderHistory();
updateReportContactRequirements();
reportCountryValue.value = reportCountrySelect.value;
updateOfficialReportingService();
