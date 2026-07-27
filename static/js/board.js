(() => {
  "use strict";

  const grid = document.querySelector("#sound-grid");
  const search = document.querySelector("#artist-search");
  const resultCount = document.querySelector("#results-count");
  const noResults = document.querySelector("#no-results");
  const clearSearch = document.querySelector("#clear-search");
  const nowPlaying = document.querySelector("#now-playing");
  const nowPlayingText = document.querySelector("#now-playing-text");
  const items = Array.from(document.querySelectorAll(".sound-item"));
  const maxPolyphony = 4;
  const activePlayers = [];

  if (!grid || !search) {
    return;
  }

  const visibleTiles = () =>
    items
      .filter((item) => !item.hidden)
      .map((item) => item.querySelector(".sound-tile"))
      .filter(Boolean);

  const removePlayer = (audio) => {
    const index = activePlayers.indexOf(audio);
    if (index >= 0) {
      activePlayers.splice(index, 1);
    }
  };

  const stopTile = (tile, audio) => {
    tile.classList.remove("is-playing");
    tile.removeAttribute("aria-pressed");
    removePlayer(audio);
    if (activePlayers.length === 0) {
      nowPlaying.classList.remove("is-visible");
    }
  };

  const playTile = async (tile) => {
    const audio = tile.querySelector("audio");
    if (!audio) {
      return;
    }

    removePlayer(audio);
    while (activePlayers.length >= maxPolyphony) {
      const oldest = activePlayers.shift();
      if (oldest) {
        oldest.pause();
        oldest.currentTime = 0;
        const oldestTile = oldest.closest(".sound-tile");
        if (oldestTile) {
          oldestTile.classList.remove("is-playing");
          oldestTile.removeAttribute("aria-pressed");
        }
      }
    }

    try {
      if (audio.readyState === HTMLMediaElement.HAVE_NOTHING) {
        audio.load();
      }
      audio.currentTime = 0;
      activePlayers.push(audio);
      tile.classList.remove("is-playing");
      // Restart the CSS animation when a tile is hit repeatedly.
      void tile.offsetWidth;
      tile.classList.add("is-playing");
      tile.setAttribute("aria-pressed", "true");

      const artist = tile.dataset.artist;
      const phrase = tile.dataset.phrase;
      nowPlayingText.textContent = `${artist} — ${phrase}`;
      nowPlaying.classList.add("is-visible");

      await audio.play();
    } catch (error) {
      console.warn("Bhangra Board audio playback was blocked", error);
      stopTile(tile, audio);
      nowPlayingText.textContent = "Audio could not start — tap again";
      nowPlaying.classList.add("is-visible");
    }
  };

  items.forEach((item) => {
    const tile = item.querySelector(".sound-tile");
    const audio = tile?.querySelector("audio");
    const image = tile?.querySelector("img");

    tile?.addEventListener("click", () => playTile(tile));
    audio?.addEventListener("ended", () => stopTile(tile, audio));
    audio?.addEventListener("error", () => {
      tile?.classList.add("has-audio-error");
      tile?.setAttribute(
        "aria-label",
        `${tile.dataset.phrase} by ${tile.dataset.artist} is unavailable`
      );
    });
    image?.addEventListener("error", () => image.classList.add("is-missing"));
  });

  const updateResults = () => {
    const query = search.value.trim().toLocaleLowerCase();
    let visibleCount = 0;

    items.forEach((item) => {
      const haystack = `${item.dataset.artist} ${item.dataset.phrase}`;
      const matches = !query || haystack.includes(query);
      item.hidden = !matches;
      if (matches) {
        visibleCount += 1;
      }
    });

    noResults.hidden = visibleCount !== 0;
    grid.hidden = visibleCount === 0;
    resultCount.textContent = `${visibleCount} ${
      visibleCount === 1 ? "sound" : "sounds"
    } ready`;

    visibleTiles().forEach((tile, index) => {
      const badge = tile.querySelector(".key-badge");
      if (!badge) {
        return;
      }
      if (index < 10) {
        badge.textContent = index === 9 ? "0" : String(index + 1);
      } else {
        badge.textContent = "";
      }
    });
  };

  search.addEventListener("input", updateResults);
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      search.value = "";
      updateResults();
      search.blur();
    } else if (event.key === "Enter") {
      search.blur();
    }
  });

  clearSearch?.addEventListener("click", () => {
    search.value = "";
    updateResults();
    search.focus();
  });

  document.addEventListener("keydown", (event) => {
    if (
      event.metaKey ||
      event.ctrlKey ||
      event.altKey ||
      event.target instanceof HTMLInputElement ||
      event.target instanceof HTMLTextAreaElement
    ) {
      return;
    }

    if (event.key === "/") {
      event.preventDefault();
      search.focus();
      return;
    }

    if (/^[0-9]$/.test(event.key)) {
      const index = event.key === "0" ? 9 : Number(event.key) - 1;
      const tile = visibleTiles()[index];
      if (tile) {
        event.preventDefault();
        playTile(tile);
      }
    }
  });

  document.querySelectorAll("[data-dialog-open]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      const dialog = document.getElementById(trigger.dataset.dialogOpen);
      if (dialog instanceof HTMLDialogElement) {
        dialog.showModal();
      }
    });
  });

  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) {
        dialog.close();
      }
    });
  });
})();
