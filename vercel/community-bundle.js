(() => {
  // web/popup-wallet.mjs
  var wallets = /* @__PURE__ */ new Map();
  window.addEventListener("eip6963:announceProvider", (e) => {
    const d = e.detail;
    if (d?.info?.uuid && typeof d.provider?.request === "function" && !wallets.has(d.info.uuid)) wallets.set(d.info.uuid, d);
  });
  window.dispatchEvent(new Event("eip6963:requestProvider"));
  function availableWallets() {
    window.dispatchEvent(new Event("eip6963:requestProvider"));
    const list = [...wallets.values()];
    if (!list.length && typeof window.ethereum?.request === "function") list.push({ info: { name: "Browser wallet" }, provider: window.ethereum });
    return list;
  }
  function requestConnection(provider2) {
    return provider2.request({ method: "eth_requestAccounts" });
  }

  // web/community-entry.js
  var $ = (id) => document.getElementById(id);
  var status = (t) => $("status").textContent = t;
  var provider;
  var account = "";
  var points = 0;
  var pair = null;
  var pending = null;
  var revision = 0;
  var currentScreen = "connect";
  var busy = false;
  var connecting = false;
  var detach = () => {
  };
  async function api(path, data) {
    const r = await fetch((location.pathname.startsWith("/web/") ? "/api/community/" : "/api/") + path, data ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) } : {});
    const v = await r.json();
    if (!r.ok) throw Error(v.error || "Please try again.");
    return v;
  }
  function enabled(v) {
    $("left").disabled = $("right").disabled = !v;
  }
  function screen(name) {
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
    const rev = ++revision;
    points = 0;
    pair = pending = null;
    busy = false;
    enabled(false);
    $("retry").hidden = true;
    $("first-vote").hidden = true;
    clearMedia();
    $("leaders").replaceChildren();
    $("wallet-dialog").close();
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
  async function connect(wallet) {
    if (connecting) return;
    connecting = true;
    $("connect").disabled = true;
    $("wallet-dialog").close();
    status("Approve the connection in your wallet popup.");
    try {
      detach();
      provider = wallet.provider;
      const current = provider, onAccounts = (a) => {
        if (current === provider) switchAccount(a).catch((e) => status(e.message));
      }, onDisconnect = () => {
        if (current === provider) switchAccount([]);
      };
      provider.on?.("accountsChanged", onAccounts);
      provider.on?.("disconnect", onDisconnect);
      detach = () => {
        current.removeListener?.("accountsChanged", onAccounts);
        current.removeListener?.("disconnect", onDisconnect);
      };
      const accounts = await requestConnection(current);
      if (current === provider) await switchAccount(accounts);
    } catch (e) {
      status(e.code === 4001 ? "Connection cancelled. Try again whenever you\u2019re ready." : e.code === -32002 ? "A connection request is already open. Check your wallet extension." : e.message || "Unable to connect.");
    } finally {
      connecting = false;
      $("connect").disabled = false;
    }
  }
  $("connect").onclick = () => {
    const wallets2 = availableWallets();
    if (wallets2.length === 1) {
      connect(wallets2[0]);
      return;
    }
    const list = $("wallet-list");
    list.replaceChildren();
    $("wallet-help").textContent = wallets2.length ? "Choose the wallet whose connection popup you want to open." : "No browser wallet detected. Install a browser wallet, or open this site inside your mobile wallet\u2019s browser, then try again.";
    for (const wallet of wallets2) {
      const button = document.createElement("button");
      button.textContent = wallet.info.name || "Browser wallet";
      button.onclick = () => connect(wallet);
      list.append(button);
    }
    $("wallet-dialog").showModal();
  };
  $("wallet-close").onclick = () => $("wallet-dialog").close();
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
    detach();
    provider = null;
    await switchAccount([]);
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
})();
