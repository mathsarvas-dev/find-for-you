"use client";

import { useId, useRef, useState } from "react";
import {
  ACCEPT_ATTR,
  ApiError,
  formatBytes,
  uploadResume,
  validateResumeFile,
} from "@/lib/profile";

type UploadStepProps = {
  onUploaded: (result: { id: number }, file: File) => void;
};

/** Escolha e envio do currículo, com validação de tipo e tamanho antes de enviar. */
export function UploadStep({ onUploaded }: UploadStepProps) {
  const inputId = useId();
  const hintId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [dragging, setDragging] = useState(false);
  const sendingRef = useRef(false);

  function pick(next: File | undefined) {
    if (!next) return;
    setFile(next);
    setFailed(null);
    setInvalid(validateResumeFile(next));
  }

  async function send() {
    if (!file || invalid || sendingRef.current) return;
    sendingRef.current = true;
    setSending(true);
    setFailed(null);
    try {
      const result = await uploadResume(file);
      onUploaded(result, file);
    } catch (err) {
      setFailed(err instanceof ApiError ? err.message : "O envio falhou. Tente de novo.");
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  }

  const valid = file !== null && invalid === null;
  const error = invalid ?? failed;

  return (
    <div className="upload">
      <label className="field__label" htmlFor={inputId}>
        Arquivo do currículo
      </label>
      <p className="field__hint" id={hintId}>
        PDF ou DOCX com texto selecionável, até 5 MB.
      </p>
      <div
        className="dropzone"
        data-dragging={dragging || undefined}
        data-invalid={error ? true : undefined}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          pick(e.dataTransfer.files[0]);
        }}
      >
        <input
          id={inputId}
          className="dropzone__input"
          type="file"
          accept={ACCEPT_ATTR}
          aria-describedby={hintId}
          aria-invalid={error ? true : undefined}
          disabled={sending}
          onChange={(e) => pick(e.target.files?.[0])}
        />
        {file ? (
          <span className="dropzone__file">
            <span className="dropzone__name">{file.name}</span>
            <span className="mono dropzone__size">{formatBytes(file.size)}</span>
          </span>
        ) : (
          <span className="dropzone__empty">Nenhum arquivo escolhido. Clique ou arraste o arquivo para cá.</span>
        )}
        <span className="dropzone__action" aria-hidden="true">
          {file ? "Trocar arquivo" : "Escolher arquivo"}
        </span>
      </div>

      {error && (
        <p className="field__error" role="alert">
          {error}
        </p>
      )}

      <div className="actions">
        <button type="button" className="button" onClick={send} disabled={!valid || sending}>
          {sending ? "Enviando…" : "Enviar currículo"}
        </button>
      </div>
    </div>
  );
}
