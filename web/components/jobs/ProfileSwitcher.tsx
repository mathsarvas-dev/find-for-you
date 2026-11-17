"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiError } from "@/lib/profile";
import {
  MAX_PROFILE_NAME,
  activateSearchProfile,
  createSearchProfile,
  deleteSearchProfile,
  listSearchProfiles,
  renameSearchProfile,
  validProfileName,
  type NamedProfile,
} from "@/lib/searchProfiles";

type Props = {
  /** Depois de trocar o perfil ativo: a página relê filtros, consulta e vagas. */
  onSwitched: () => void;
  disabled?: boolean;
};

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Algo deu errado ao falar com a API. Tente de novo.";

/** Perfis de busca nomeados: trocar, criar, renomear e excluir. */
export function ProfileSwitcher({ onSwitched, disabled = false }: Props) {
  const [profiles, setProfiles] = useState<NamedProfile[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [managing, setManaging] = useState(false);
  const [newName, setNewName] = useState("");
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const selectId = useId();
  const newRef = useRef<HTMLInputElement>(null);
  const renameRef = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    try {
      const list = await listSearchProfiles();
      if (mounted.current) setProfiles(list);
    } catch (err) {
      if (mounted.current) {
        setProfiles((p) => p ?? []);
        setError(message(err));
      }
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    listSearchProfiles()
      .then((list) => {
        if (!cancelled) setProfiles(list);
      })
      .catch((err) => {
        if (cancelled) return;
        setProfiles([]);
        setError(message(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (creating) newRef.current?.focus();
  }, [creating]);
  useEffect(() => {
    if (renamingId !== null) renameRef.current?.focus();
  }, [renamingId]);

  /** Roda uma ação; erro vira aviso (alert) e a ação não é repetida sozinha. */
  async function run(action: () => Promise<void>): Promise<boolean> {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      return true;
    } catch (err) {
      if (mounted.current) setError(message(err));
      return false;
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  const list = profiles ?? [];
  const active = list.find((p) => p.active);
  const locked = disabled || busy || profiles === null;

  async function switchTo(id: number) {
    if (active?.id === id) return;
    const ok = await run(async () => {
      await activateSearchProfile(id);
      await reload();
    });
    if (ok && mounted.current) onSwitched();
  }

  async function create() {
    const name = newName.trim();
    if (!validProfileName(name)) return;
    const ok = await run(async () => {
      await createSearchProfile(name, true);
      await reload();
    });
    if (ok && mounted.current) {
      setCreating(false);
      setNewName("");
      setNotice(`Perfil “${name}” criado a partir dos filtros atuais.`);
    }
  }

  async function rename(id: number) {
    const name = renameValue.trim();
    if (!validProfileName(name)) return;
    const ok = await run(async () => {
      await renameSearchProfile(id, name);
      await reload();
    });
    if (ok && mounted.current) setRenamingId(null);
  }

  async function remove(p: NamedProfile) {
    const ok = await run(async () => {
      await deleteSearchProfile(p.id);
      await reload();
    });
    if (ok && mounted.current) {
      setDeletingId(null);
      setNotice(`Perfil “${p.name}” excluído.`);
    }
  }

  return (
    <section className="profile-switcher" aria-label="Perfis de busca">
      <div className="profile-switcher__row">
        <label htmlFor={selectId} className="profile-switcher__label">
          Perfil de busca
        </label>
        <select
          id={selectId}
          className="input profile-switcher__select"
          value={active?.id ?? ""}
          disabled={locked || list.length === 0}
          onChange={(e) => void switchTo(Number(e.target.value))}
        >
          {list.length === 0 && <option value="">Padrão</option>}
          {list.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="button button--secondary"
          disabled={locked || creating}
          onClick={() => {
            setCreating(true);
            setManaging(false);
          }}
        >
          Novo perfil
        </button>
        {list.length > 0 && (
          <button
            type="button"
            className="button button--secondary"
            aria-expanded={managing}
            onClick={() => {
              setManaging((m) => !m);
              setCreating(false);
            }}
          >
            Gerenciar perfis
          </button>
        )}
      </div>

      {creating && (
        <form
          className="profile-switcher__form"
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <label className="sr-only" htmlFor={`${selectId}-new`}>
            Nome do perfil
          </label>
          <input
            id={`${selectId}-new`}
            ref={newRef}
            className="input"
            value={newName}
            maxLength={MAX_PROFILE_NAME}
            placeholder="Nome do perfil, ex.: Remoto Europa"
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setCreating(false);
            }}
          />
          <button type="submit" className="button" disabled={busy || !validProfileName(newName)}>
            Criar
          </button>
          <button type="button" className="button button--secondary" onClick={() => setCreating(false)}>
            Cancelar
          </button>
          <p className="profile-switcher__hint">O novo perfil começa com os filtros e a consulta de gosto atuais.</p>
        </form>
      )}

      {managing && list.length > 0 && (
        <ul className="profile-switcher__list">
          {list.map((p) => (
            <li key={p.id} className="profile-switcher__item">
              {renamingId === p.id ? (
                <form
                  className="profile-switcher__form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void rename(p.id);
                  }}
                >
                  <label className="sr-only" htmlFor={`${selectId}-rename`}>
                    Novo nome de {p.name}
                  </label>
                  <input
                    id={`${selectId}-rename`}
                    ref={renameRef}
                    className="input"
                    value={renameValue}
                    maxLength={MAX_PROFILE_NAME}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Escape") setRenamingId(null);
                    }}
                  />
                  <button type="submit" className="button" disabled={busy || !validProfileName(renameValue)}>
                    Salvar nome
                  </button>
                  <button type="button" className="button button--secondary" onClick={() => setRenamingId(null)}>
                    Cancelar
                  </button>
                </form>
              ) : deletingId === p.id ? (
                <>
                  <span>Excluir o perfil “{p.name}”? Isso não pode ser desfeito.</span>
                  <button type="button" className="button" disabled={busy} onClick={() => void remove(p)}>
                    Confirmar exclusão
                  </button>
                  <button type="button" className="button button--secondary" onClick={() => setDeletingId(null)}>
                    Cancelar
                  </button>
                </>
              ) : (
                <>
                  <span className="profile-switcher__name">
                    {p.name}
                    {p.active && <span className="profile-switcher__badge"> (ativo)</span>}
                  </span>
                  <button
                    type="button"
                    className="link-button"
                    disabled={busy}
                    aria-label={`Renomear ${p.name}`}
                    onClick={() => {
                      setRenamingId(p.id);
                      setRenameValue(p.name);
                      setDeletingId(null);
                    }}
                  >
                    Renomear
                  </button>
                  <button
                    type="button"
                    className="link-button link-button--danger"
                    disabled={busy || p.active}
                    aria-label={`Excluir ${p.name}`}
                    title={p.active ? "Ative outro perfil antes de excluir este." : undefined}
                    onClick={() => {
                      setDeletingId(p.id);
                      setRenamingId(null);
                    }}
                  >
                    Excluir
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <p className="profile-switcher__live" role="status">
        {notice}
      </p>
      {error && (
        <p className="field__error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
