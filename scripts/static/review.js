(() => {
  "use strict";

  const state = {
    queue: null,
    reviews: {},
    index: 0,
  };

  const elements = {
    empty: document.querySelector("#empty-state"),
    card: document.querySelector("#review-card"),
    progressCount: document.querySelector("#progress-count"),
    progressBar: document.querySelector("#progress-bar"),
    position: document.querySelector("#clip-position"),
    grade: document.querySelector("#clip-grade"),
    artist: document.querySelector("#clip-artist"),
    phrase: document.querySelector("#clip-phrase"),
    variants: document.querySelector("#variant-grid"),
    scores: document.querySelector("#score-grid"),
    notes: document.querySelector("#review-notes"),
    saveState: document.querySelector("#save-state"),
    previous: document.querySelector("#previous-clip"),
    next: document.querySelector("#next-clip"),
    save: document.querySelector("#save-review"),
    saveNext: document.querySelector("#save-next"),
    variantTemplate: document.querySelector("#variant-template"),
    scoreTemplate: document.querySelector("#score-template"),
  };

  const currentClip = () => state.queue?.clips[state.index];

  const stopOtherAudio = (selected) => {
    document.querySelectorAll("audio").forEach((audio) => {
      if (audio !== selected) {
        audio.pause();
        audio.currentTime = 0;
      }
    });
  };

  const renderVariants = (clip, review) => {
    elements.variants.replaceChildren();
    clip.variants.forEach((variant, index) => {
      const fragment = elements.variantTemplate.content.cloneNode(true);
      const card = fragment.querySelector(".variant-card");
      const input = fragment.querySelector("input");
      const audio = fragment.querySelector("audio");
      fragment.querySelector(".variant-key").textContent =
        index < 9 ? String(index + 1) : "";
      fragment.querySelector(".variant-label").textContent = variant.label;
      input.value = variant.id;
      input.checked = review?.selected_variant === variant.id;
      audio.src = `/media/${variant.path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`;
      audio.addEventListener("play", () => stopOtherAudio(audio));
      audio.addEventListener("play", () => {
        input.checked = true;
      });
      card.addEventListener("click", (event) => {
        if (event.target instanceof Element && event.target.closest("audio")) {
          return;
        }
        input.checked = true;
        audio.currentTime = 0;
        audio.play().catch(() => {});
      });
      card.dataset.variantId = variant.id;
      elements.variants.append(fragment);
    });
  };

  const renderScores = (review) => {
    elements.scores.replaceChildren();
    state.queue.quality_fields.forEach((field) => {
      const fragment = elements.scoreTemplate.content.cloneNode(true);
      fragment.querySelector("legend").textContent = field.label;
      fragment.querySelector("p").textContent = field.help;
      const options = fragment.querySelector(".score-options");
      for (let score = 1; score <= 5; score += 1) {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "radio";
        input.name = `score-${field.id}`;
        input.value = String(score);
        input.checked = review?.scores?.[field.id] === score;
        const text = document.createElement("span");
        text.textContent = String(score);
        label.append(input, text);
        options.append(label);
      }
      elements.scores.append(fragment);
    });
  };

  const render = () => {
    const clips = state.queue?.clips ?? [];
    if (clips.length === 0) {
      elements.empty.hidden = false;
      elements.card.hidden = true;
      return;
    }

    elements.empty.hidden = true;
    elements.card.hidden = false;
    const clip = currentClip();
    const review = state.reviews[clip.id];
    const reviewedCount = Object.keys(state.reviews).filter((clipId) =>
      clips.some((candidate) => candidate.id === clipId)
    ).length;
    elements.progressCount.textContent = `${reviewedCount} / ${clips.length}`;
    elements.progressBar.style.width = `${(reviewedCount / clips.length) * 100}%`;
    elements.position.textContent = `${state.index + 1} of ${clips.length}`;
    elements.previous.disabled = state.index === 0;
    elements.next.disabled = state.index === clips.length - 1;
    elements.grade.textContent = `Current grade ${clip.current_grade ?? "—"}`;
    elements.artist.textContent = clip.artist;
    elements.phrase.textContent = clip.phrase;
    elements.notes.value = review?.notes ?? "";
    elements.saveState.textContent = review ? "Saved" : "Not reviewed";

    renderVariants(clip, review);
    renderScores(review);
    document.querySelectorAll('input[name="decision"]').forEach((input) => {
      input.checked = review?.decision === input.value;
    });
  };

  const collectReview = () => {
    const selected = document.querySelector(
      'input[name="selected-variant"]:checked'
    );
    const decision = document.querySelector('input[name="decision"]:checked');
    if (!decision) {
      throw new Error("Choose approve, hold, or replace source.");
    }
    if (decision.value === "approve" && !selected) {
      throw new Error("Choose a variant before approving.");
    }
    const scores = {};
    state.queue.quality_fields.forEach((field) => {
      const input = document.querySelector(
        `input[name="score-${field.id}"]:checked`
      );
      if (input) {
        scores[field.id] = Number(input.value);
      }
    });
    return {
      decision: decision.value,
      selected_variant: selected?.value ?? null,
      scores,
      notes: elements.notes.value,
    };
  };

  const save = async (moveNext) => {
    try {
      const clip = currentClip();
      const payload = collectReview();
      elements.saveState.textContent = "Saving…";
      const response = await fetch(`/api/reviews/${encodeURIComponent(clip.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || "Review could not be saved.");
      }
      state.reviews[clip.id] = result.review;
      elements.saveState.textContent = "Saved";
      if (moveNext && state.index < state.queue.clips.length - 1) {
        state.index += 1;
      }
      render();
    } catch (error) {
      elements.saveState.textContent = error.message;
    }
  };

  const move = (change) => {
    const nextIndex = state.index + change;
    if (nextIndex >= 0 && nextIndex < state.queue.clips.length) {
      stopOtherAudio(null);
      state.index = nextIndex;
      render();
    }
  };

  elements.previous.addEventListener("click", () => move(-1));
  elements.next.addEventListener("click", () => move(1));
  elements.save.addEventListener("click", () => save(false));
  elements.saveNext.addEventListener("click", () => save(true));

  document.addEventListener("keydown", (event) => {
    const isTyping =
      event.target instanceof HTMLTextAreaElement ||
      (event.target instanceof HTMLInputElement &&
        !["radio", "checkbox"].includes(event.target.type));
    if (
      event.metaKey ||
      event.ctrlKey ||
      event.altKey ||
      isTyping
    ) {
      return;
    }
    if (/^[1-9]$/.test(event.key)) {
      const audio = elements.variants.querySelectorAll("audio")[Number(event.key) - 1];
      if (audio) {
        event.preventDefault();
        audio.currentTime = 0;
        audio.play().catch(() => {});
      }
    } else if (event.key === "ArrowLeft") {
      move(-1);
    } else if (event.key === "ArrowRight") {
      move(1);
    }
  });

  fetch("/api/queue")
    .then((response) => response.json())
    .then((payload) => {
      state.queue = payload.queue;
      state.reviews = payload.reviews;
      render();
    })
    .catch((error) => {
      elements.empty.hidden = false;
      elements.empty.querySelector("p").textContent = error.message;
    });
})();
