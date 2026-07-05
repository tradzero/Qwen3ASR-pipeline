import { useState } from "react";

function firstUsefulLine(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line && !line.startsWith("#")) || "";
}

function stripQuotes(text) {
  const trimmed = text.trim();
  if (trimmed.length >= 2 && trimmed[0] === trimmed[trimmed.length - 1] && ["'", '"'].includes(trimmed[0])) {
    return trimmed.slice(1, -1).trim();
  }
  return trimmed;
}

function decodePathname(pathname) {
  try {
    return decodeURIComponent(pathname);
  } catch {
    return pathname;
  }
}

export function normalizePathText(rawText) {
  let text = stripQuotes(firstUsefulLine(rawText));
  if (!text.toLowerCase().startsWith("file://")) {
    return text;
  }

  try {
    const url = new URL(text);
    let pathname = decodePathname(url.pathname || "");
    if (url.hostname) {
      return `\\\\${url.hostname}${pathname.replaceAll("/", "\\")}`;
    }
    if (/^\/[A-Za-z]:\//.test(pathname)) {
      pathname = pathname.slice(1);
    }
    return pathname.replaceAll("/", "\\");
  } catch {
    return text;
  }
}

export function PathDropInput({ label, value, onChange, placeholder }) {
  const [dragging, setDragging] = useState(false);
  const [notice, setNotice] = useState("");

  const applyText = (text) => {
    const nextValue = normalizePathText(text);
    if (!nextValue) {
      return false;
    }
    onChange(nextValue);
    setNotice("");
    return true;
  };

  const handleDrop = (event) => {
    event.preventDefault();
    setDragging(false);

    const droppedText = event.dataTransfer.getData("text/uri-list") || event.dataTransfer.getData("text/plain");
    if (droppedText && applyText(droppedText)) {
      return;
    }
    if (event.dataTransfer.files?.length) {
      setNotice("浏览器没有提供真实路径，请使用复制为路径后粘贴。");
    }
  };

  const handlePaste = (event) => {
    const pastedText = event.clipboardData.getData("text/plain");
    const normalized = normalizePathText(pastedText);
    if (normalized && normalized !== pastedText.trim()) {
      event.preventDefault();
      onChange(normalized);
      setNotice("");
    }
  };

  const handleBlur = () => {
    const normalized = normalizePathText(value);
    if (normalized && normalized !== value) {
      onChange(normalized);
    }
  };

  return (
    <label className={dragging ? "path-drop active" : "path-drop"}>
      {label}
      <input
        onBlur={handleBlur}
        onChange={(event) => onChange(event.target.value)}
        onDragEnter={() => setDragging(true)}
        onDragLeave={() => setDragging(false)}
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
        onPaste={handlePaste}
        placeholder={placeholder}
        value={value}
      />
      {notice ? <span className="path-drop-notice">{notice}</span> : null}
    </label>
  );
}
