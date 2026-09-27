(() => {
  // web/community-entry.js
  var $ = (id) => document.getElementById(id);
  var status = (t) => $("status").textContent = t;
  var rememberedKey = "flybrains.rememberedAddress.v1";
  var validAddress = (a) => typeof a === "string" && /^0x[a-fA-F0-9]{40}$/.test(a) && !/^0x0{40}$/i.test(a);
  function rememberAddress(a) {
    try {
      if (a) localStorage.setItem(rememberedKey, a.toLowerCase());
      else localStorage.removeItem(rememberedKey);
      return true;
    } catch {
      return false;
    }
  }
  function rememberedAddress() {
    try {
      const a = localStorage.getItem(rememberedKey);
      if (validAddress(a)) return a.toLowerCase();
      if (a) localStorage.removeItem(rememberedKey);
    } catch {
    }
    return "";
  }
  var account = "";
  var points = 0;
  var pair = null;
  var pending = null;
  var revision = 0;
  var currentScreen = "connect";
  var busy = false;
  async function api(path, data) {
    const r = await fetch((location.pathname.startsWith("/web/") ? "/api/community/" : "/api/") + path, data ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) } : {});
    const v = await r.json();
    if (!r.ok) throw Error(v.error || "Please try again.");
    return v;
  }
  function enabled(v) {
    $("left").disabled = $("right").disabled = !v;
  }
  function suggestionGate() {
    const b = $("suggestion-open");
    b.disabled = points < 100;
    b.textContent = points < 100 ? `Suggestion box \xB7 ${points}/100` : "Suggest a trait \u2197";
  }
  function screen(name) {
    suggestionGate();
    currentScreen = name;
    for (const k of ["connect", "nickname", "welcome"]) $(k + "-gate").hidden = name !== k;
    $("site").hidden = name !== "site";
    $("leaderboard-view").hidden = name !== "leaders";
    $("profile-controls").hidden = !account;
    for (const id of ["leaderboard-open", "welcome-leaderboard"]) $(id).hidden = !account || points < 1;
    document.querySelector('[data-screen="' + name + '"]')?.focus();
  }
  function clearMedia() {
    for (const id of ["left-video", "right-video"]) {
      const v = $(id);
      v.pause();
      v.removeAttribute("src");
      v.removeAttribute("poster");
      v.load();
    }
  }
  function welcome() {
    $("welcome-name").textContent = $("identity").textContent;
    screen("welcome");
    status("");
  }
  async function switchAccount(accounts) {
    const next = (accounts[0] || "").toLowerCase();
    if (next === account) return;
    account = next;
    $("suggestion-dialog").close();
    $("suggestion-title").value = $("suggestion-details").value = "";
    const rev = ++revision;
    points = 0;
    pair = pending = null;
    busy = false;
    enabled(false);
    $("retry").hidden = true;
    $("first-vote").hidden = true;
    clearMedia();
    $("leaders").replaceChildren();
    screen("connect");
    status("");
    $("identity").textContent = "";
    $("points").textContent = "0";
    if (!account) return;
    const a = account;
    status("Loading your profile\u2026");
    try {
      const info = await api("account", { address: a });
      if (rev !== revision || a !== account) return;
      points = info.points;
      $("points").textContent = String(points);
      $("identity").textContent = info.display;
      const fallback = a.slice(0, 5) + "\u2026" + a.slice(-4);
      const nickname = info.nickname ?? (info.display === fallback ? "" : info.display);
      $("nickname").value = nickname;
      status("");
      if (nickname.trim()) welcome();
      else screen("nickname");
    } catch (e) {
      if (rev === revision) {
        account = "";
        screen("connect");
        status(e.message);
      }
    }
  }
  async function media(video, record) {
    video.poster = record.poster;
    video.src = record.preview;
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error("Media timed out")), 15e3);
      video.oncanplay = () => {
        clearTimeout(timer);
        resolve();
      };
      video.onerror = () => {
        clearTimeout(timer);
        reject(Error("Preview unavailable"));
      };
      video.load();
    });
    await video.play();
  }
  async function nextPair(attempt = 0) {
    if (!account || currentScreen !== "site") return;
    enabled(false);
    pair = pending = null;
    const rev = ++revision, a = account;
    status("Loading both artworks\u2026");
    try {
      const next = await api("pair", { address: a });
      if (rev !== revision || a !== account) return;
      await Promise.all([media($("left-video"), next.left), media($("right-video"), next.right)]);
      if (rev !== revision || a !== account) return;
      pair = next;
      enabled(true);
      status("Which artwork do you prefer?");
    } catch (e) {
      if (rev !== revision) return;
      if (attempt < 3) {
        status("Replacing unavailable previews\u2026");
        setTimeout(() => {
          if (rev === revision && a === account) nextPair(attempt + 1);
        }, 800);
      } else status("Previews are unavailable. Return to Welcome and try again; no choice was recorded.");
    }
  }
  async function choose(choice) {
    if (!pair || !account || busy) return;
    busy = true;
    enabled(false);
    const a = account, rev = revision;
    pending = pending || { address: a, receipt: pair.receipt, choice };
    status("Saving your comparison\u2026");
    try {
      const result = await api("vote", pending);
      if (rev !== revision || a !== account) return;
      const first = points === 0;
      points = result.points;
      suggestionGate();
      $("points").textContent = String(points);
      pending = null;
      $("retry").hidden = true;
      $("leaderboard-open").hidden = points < 1;
      if (first) $("first-vote").hidden = false;
      await nextPair();
    } catch (e) {
      if (a === account && rev === revision) {
        status("Not confirmed. Retry safely to check the same choice.");
        $("retry").hidden = false;
      }
    } finally {
      busy = false;
    }
  }
  async function leaderboard() {
    if (points < 1 || !account) return;
    revision++;
    pending = pair = null;
    enabled(false);
    clearMedia();
    $("retry").hidden = true;
    screen("leaders");
    status("Loading contributions\u2026");
    const a = account, rev = revision;
    try {
      const rows = await api("leaderboard");
      if (a !== account || rev !== revision) return;
      $("leaders").replaceChildren();
      for (const r of rows) {
        const li = document.createElement("li"), name = document.createElement("span"), score = document.createElement("strong");
        name.textContent = r.display;
        score.textContent = r.points + " comparisons";
        li.append(name, score);
        $("leaders").append(li);
      }
      status("");
    } catch (e) {
      if (rev === revision) status(e.message);
    }
  }
  $("address-form").onsubmit = async (e) => {
    e.preventDefault();
    const field = $("address"), value = field.value.trim();
    if (!/^0x[a-fA-F0-9]{40}$/.test(value) || /^0x0{40}$/i.test(value)) {
      field.setCustomValidity("Enter a public Ethereum address: 0x followed by 40 hexadecimal characters.");
      field.reportValidity();
      return;
    }
    field.setCustomValidity("");
    $("connect").disabled = true;
    try {
      await switchAccount([value]);
      if (account === value.toLowerCase() && !rememberAddress($("remember-address").checked ? account : "")) status("You can continue, but this browser could not remember your address.");
    } finally {
      $("connect").disabled = false;
    }
  };
  $("address").oninput = () => $("address").setCustomValidity("");
  $("nickname-form").onsubmit = async (e) => {
    e.preventDefault();
    if (!account) return;
    const value = $("nickname").value.trim();
    if (!value) {
      $("nickname").setCustomValidity("Please choose a nickname to continue.");
      $("nickname").reportValidity();
      return;
    }
    const a = account, rev = revision;
    $("nickname-save").disabled = true;
    try {
      const info = await api("account", { address: a, nickname: value });
      if (a !== account || rev !== revision) return;
      $("identity").textContent = info.display;
      welcome();
    } catch (e2) {
      status(e2.message);
    } finally {
      $("nickname-save").disabled = false;
    }
  };
  $("nickname").oninput = () => $("nickname").setCustomValidity("");
  $("edit-nickname").onclick = () => {
    revision++;
    pending = pair = null;
    enabled(false);
    clearMedia();
    $("retry").hidden = true;
    screen("nickname");
    status("");
  };
  $("disconnect").onclick = async () => {
    const cleared = rememberAddress("");
    await switchAccount([]);
    $("address").value = "";
    $("address").focus();
    if (!cleared) status("This browser could not clear the saved address. Clear this site\u2019s browser data to forget it.");
  };
  $("remember-address").onchange = () => {
    if (!$("remember-address").checked) rememberAddress("");
  };
  for (const id of ["welcome-start", "back-to-curation"]) $(id).onclick = () => {
    screen("site");
    nextPair();
  };
  for (const id of ["leaderboard-open", "welcome-leaderboard"]) $(id).onclick = () => leaderboard();
  $("home").onclick = () => {
    if (!account) return;
    revision++;
    pair = pending = null;
    clearMedia();
    welcome();
  };
  $("left").onclick = () => choose("left");
  $("right").onclick = () => choose("right");
  $("retry").onclick = () => {
    if (pending) {
      $("retry").hidden = true;
      choose(pending.choice);
    }
  };
  window.communityConnection = { onAccountsChanged: switchAccount };
  screen("connect");
  var suggestionBusy = false;
  $("suggestion-open").onclick = async () => {
    if (points < 100 || !account) return;
    const a = account;
    $("suggestion-status").textContent = "Loading\u2026";
    $("suggestion-save").disabled = true;
    $("suggestion-dialog").showModal();
    try {
      const r = await api("suggestion", { address: a });
      if (a !== account) return;
      $("suggestion-title").value = r.suggestion?.title || "";
      $("suggestion-details").value = r.suggestion?.details || "";
      $("suggestion-status").textContent = "";
      $("suggestion-save").disabled = false;
    } catch (e) {
      if (a === account) $("suggestion-status").textContent = e.message;
    }
  };
  $("suggestion-close").onclick = () => $("suggestion-dialog").close();
  $("suggestion-form").onsubmit = async (e) => {
    e.preventDefault();
    if (suggestionBusy || points < 100 || !account) return;
    const a = account;
    const title = $("suggestion-title").value.trim(), details = $("suggestion-details").value.trim();
    if (!title || !details) {
      $("suggestion-status").textContent = "Please add a name and description.";
      return;
    }
    suggestionBusy = true;
    $("suggestion-save").disabled = true;
    $("suggestion-status").textContent = "Saving\u2026";
    try {
      await api("suggestion", { address: a, title, details });
      if (a === account) $("suggestion-status").textContent = "Saved. Thanks for sharing your idea!";
    } catch (e2) {
      if (a === account) $("suggestion-status").textContent = "Not confirmed. Save again to retry safely. " + e2.message;
    } finally {
      suggestionBusy = false;
      if (a === account) $("suggestion-save").disabled = false;
    }
  };
  var remembered = rememberedAddress();
  if (remembered) {
    $("address").value = remembered;
    $("connect").disabled = true;
    switchAccount([remembered]).finally(() => {
      $("connect").disabled = false;
    });
  }
  addEventListener("storage", (e) => {
    if (e.key === rememberedKey && !e.newValue) {
      switchAccount([]);
      $("address").value = "";
    }
  });
})();