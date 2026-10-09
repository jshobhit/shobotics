/**
 * dictation.js
 * Verbatim voice-to-text for the long report textareas, using the
 * browser-native Web Speech API (SpeechRecognition). No network calls
 * made by this app, no API key, nothing sent to Anthropic/OpenAI —
 * speech-to-text happens via the browser's own engine (in Chrome/Edge
 * that engine talks to Google's speech service; Firefox/Safari support
 * is inconsistent, which is why we degrade to a disabled button below).
 *
 * Deliberately dumb: only FINAL recognition results are inserted, and
 * they're spliced in exactly as the engine returns them — no rewriting,
 * punctuation clean-up, or summarization. "1:1" was the requirement.
 */
(function (global) {
  "use strict";

  const SpeechRecognitionCtor = global.SpeechRecognition || global.webkitSpeechRecognition;
  const SUPPORTED = !!SpeechRecognitionCtor;

  /**
   * Wires a toggle button to dictate verbatim text into a textarea.
   * @param {HTMLButtonElement} button
   * @param {HTMLTextAreaElement} textarea
   * @returns {{stop: Function}|null} null if the browser can't do this at all
   */
  function attach(button, textarea) {
    const labelEl = button.querySelector(".btn-label");

    if (!SUPPORTED) {
      button.disabled = true;
      button.title = "Voice dictation isn't supported in this browser — try Chrome or Edge.";
      return null;
    }

    let recognition = null;
    let listening = false;

    function setLabel(text) {
      if (labelEl) labelEl.textContent = text;
      else button.textContent = text;
    }

    function setListening(on) {
      listening = on;
      button.classList.toggle("is-listening", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
      button.title = on ? "Listening… click to stop" : "Dictate — verbatim transcription, no edits added";
      setLabel(on ? "Listening…" : "Dictate");
    }

    /** Splice verbatim text in at the caret, adding a space only if needed. */
    function insertAtCursor(text) {
      if (!text) return;
      const hasSelection = typeof textarea.selectionStart === "number";
      const start = hasSelection ? textarea.selectionStart : textarea.value.length;
      const end = hasSelection ? textarea.selectionEnd : textarea.value.length;
      const before = textarea.value.slice(0, start);
      const after = textarea.value.slice(end);
      const needsSpaceBefore = before.length > 0 && !/\s$/.test(before);
      const insert = (needsSpaceBefore ? " " : "") + text;
      textarea.value = before + insert + after;
      const caret = (before + insert).length;
      if (hasSelection) textarea.setSelectionRange(caret, caret);
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function start() {
      recognition = new SpeechRecognitionCtor();
      recognition.lang = navigator.language || "en-US";
      recognition.continuous = true;
      recognition.interimResults = false; // only ever commit final text — no half-formed guesses

      recognition.onresult = (e) => {
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const result = e.results[i];
          if (result.isFinal) {
            insertAtCursor(result[0].transcript.trim());
          }
        }
      };

      recognition.onerror = (e) => {
        if (e.error === "no-speech" || e.error === "aborted") return; // benign
        console.error("[dictation]", e.error);
        listening = false;
        setListening(false);
      };

      recognition.onend = () => {
        if (listening) {
          // Browsers auto-stop the engine after a pause; restart silently
          // while the user still has the mic toggled on.
          try {
            recognition.start();
          } catch (_) {
            setListening(false);
          }
        }
      };

      try {
        recognition.start();
        setListening(true);
      } catch (err) {
        console.error("[dictation] failed to start", err);
        setListening(false);
      }
    }

    function stop() {
      listening = false; // set before .stop() so onend doesn't auto-restart
      if (recognition) {
        try { recognition.stop(); } catch (_) { /* already stopped */ }
      }
      setListening(false);
    }

    button.addEventListener("click", () => {
      if (listening) stop();
      else start();
    });

    return { stop };
  }

  /**
   * Session-mode dictation for the v2 Dictation screen's single mic button.
   * Unlike attach() (which splices into a textarea), this accumulates a
   * running verbatim transcript string and reports it via callbacks, so the
   * caller owns the state and UI. Still deliberately dumb: only FINAL results
   * are appended, exactly as the engine returns them.
   *
   * @param {Object} handlers
   * @param {(fullTranscript: string) => void} [handlers.onTranscript]  called on every committed chunk
   * @param {(errorCode: string) => void}      [handlers.onError]       recognition error (non-benign)
   * @param {() => void}                        [handlers.onAutoStop]    engine stopped and could not resume
   * @returns {{start,stop,pause,resume,reset,getTranscript}|null} null if unsupported
   */
  function createSession(handlers) {
    handlers = handlers || {};
    if (!SUPPORTED) return null;

    let recognition = null;
    let running = false;      // user intends to be capturing
    let transcript = "";

    function emit() {
      if (handlers.onTranscript) handlers.onTranscript(transcript);
    }

    function append(text) {
      if (!text) return;
      transcript += (transcript && !/\s$/.test(transcript) ? " " : "") + text;
      emit();
    }

    function build() {
      const r = new SpeechRecognitionCtor();
      r.lang = navigator.language || "en-US";
      r.continuous = true;
      r.interimResults = false;

      r.onresult = (e) => {
        for (let i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) append(e.results[i][0].transcript.trim());
        }
      };
      r.onerror = (e) => {
        if (e.error === "no-speech" || e.error === "aborted") return; // benign
        // Any other error (permission denied, no mic, network) is terminal:
        // clear `running` first so onend's auto-restart doesn't spin the
        // engine into an infinite retry/error loop.
        running = false;
        console.error("[dictation:session]", e.error);
        if (handlers.onError) handlers.onError(e.error);
      };
      r.onend = () => {
        if (running) {
          // Browsers auto-stop after a pause; restart silently while capturing.
          try { r.start(); }
          catch (_) { running = false; if (handlers.onAutoStop) handlers.onAutoStop(); }
        }
      };
      return r;
    }

    function start() {
      if (running) return;
      running = true;
      recognition = build();
      try { recognition.start(); }
      catch (err) {
        console.error("[dictation:session] failed to start", err);
        running = false;
        if (handlers.onError) handlers.onError("start-failed");
      }
    }

    function stop() {
      running = false; // set before .stop() so onend doesn't auto-restart
      if (recognition) { try { recognition.stop(); } catch (_) {} }
    }

    return {
      start,
      stop,
      pause: stop,           // pause == stop the engine but keep the transcript
      resume: start,
      reset() { transcript = ""; emit(); },
      getTranscript() { return transcript; },
    };
  }

  global.Dictation = { attach, createSession, SUPPORTED };
})(window);
