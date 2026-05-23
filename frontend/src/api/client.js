export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:7860";
export const TERMINAL_STATUSES = new Set(["succeeded", "failed", "canceled", "interrupted"]);


async function readJson(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `Request failed: ${response.status}`);
  }
  return payload;
}

export async function getHealth() {
  const response = await fetch(`${API_BASE_URL}/api/health`);
  return readJson(response);
}

export async function getDefaults() {
  return readJson(await fetch(`${API_BASE_URL}/api/config/defaults`));
}

export async function listJobs() {
  return readJson(await fetch(`${API_BASE_URL}/api/jobs`));
}

export async function getJob(jobId) {
  return readJson(await fetch(`${API_BASE_URL}/api/jobs/${encodeURIComponent(jobId)}`));
}

export async function createAsrJob(payload) {
  return readJson(
    await fetch(`${API_BASE_URL}/api/jobs/asr`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function createLadaJob(payload) {
  return readJson(
    await fetch(`${API_BASE_URL}/api/jobs/lada`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function cancelJob(jobId) {
  return readJson(await fetch(`${API_BASE_URL}/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }));
}

export function artifactUrl(jobId, artifactName) {
  return `${API_BASE_URL}/api/artifacts/${encodeURIComponent(jobId)}/${encodeURIComponent(artifactName)}`;
}

export function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    const form = new FormData();
    form.append("file", file);

    request.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    request.onload = () => {
      let payload = {};
      try {
        payload = JSON.parse(request.responseText || "{}");
      } catch {
        payload = {};
      }
      if (request.status >= 200 && request.status < 300) {
        resolve(payload);
        return;
      }
      reject(new Error(payload.detail || `Upload failed: ${request.status}`));
    };
    request.onerror = () => reject(new Error("Upload request failed"));
    request.open("POST", `${API_BASE_URL}/api/uploads`);
    request.send(form);
  });
}

export function subscribeJobEvents(jobId, onEvent, onError) {
  const source = new EventSource(`${API_BASE_URL}/api/jobs/${encodeURIComponent(jobId)}/events`);
  const eventNames = ["status", "progress", "log", "artifact", "error"];
  const handleEvent = (event) => {
    try {
      onEvent(JSON.parse(event.data));
    } catch (error) {
      onError?.(error);
    }
  };

  eventNames.forEach((name) => source.addEventListener(name, handleEvent));
  source.onerror = (error) => {
    onError?.(error);
    source.close();
  };
  return () => source.close();
}