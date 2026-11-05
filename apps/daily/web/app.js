(() => {
  "use strict";

  const WEEKDAYS = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];
  const state = { feed: null, activeDate: null };

  const $ = (id) => document.getElementById(id);
  const paper = $("paper");

  function text(node, value) {
    node.textContent = value == null ? "" : String(value);
  }

  function el(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value != null) node.textContent = String(value);
    return node;
  }

  function pad(number, width) {
    return String(number).padStart(width, "0");
  }

  function dateParts(date) {
    const [year, month, day] = date.split("-").map(Number);
    return {
      month: `${month}月`,
      day: pad(day, 2),
      weekday: WEEKDAYS[new Date(year, month - 1, day).getDay()],
    };
  }

  function shortDate(date) {
    return `${date.slice(5, 7)}.${date.slice(8, 10)}`;
  }

  function charCount(body) {
    return Array.from(body.join("").replace(/\s+/g, "")).length;
  }

  function reduceMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function animatePaper() {
    paper.classList.remove("is-in");
    void paper.offsetWidth;
    paper.classList.add("is-in");
  }

  function pickIssue(date) {
    const issues = state.feed ? state.feed.issues : [];
    return issues.find((item) => item.date === date) || issues[0] || null;
  }

  function renderNotes(feed) {
    const dayNote = $("day-note");
    const hasToday = feed.issues.some((item) => item.date === feed.today);
    dayNote.hidden = !(feed.issues.length > 0 && !hasToday);

    const note = $("feed-note");
    const errors = Array.isArray(feed.errors) ? feed.errors : [];
    if (errors.length > 0) {
      text(note, `有 ${errors.length} 条日课读取问题，已跳过。`);
      note.title = errors
        .map((item) => (item.file ? `${item.file}：${item.reason}` : item.reason))
        .join("\n");
      note.hidden = false;
    } else {
      text(note, "");
      note.removeAttribute("title");
      note.hidden = true;
    }
  }

  function renderPast(issues, currentDate) {
    const past = $("past");
    const list = $("past-list");
    list.replaceChildren();
    past.hidden = issues.length === 0;
    if (issues.length === 0) return;

    for (const issue of issues) {
      const row = el("a", "past-row");
      row.href = `#${issue.date}`;
      if (issue.date === currentDate) {
        row.classList.add("is-current");
        row.setAttribute("aria-current", "page");
      }

      const head = el("span", "past-head");
      head.append(
        el("span", "past-book", issue.book.title),
        el("span", "past-title", issue.headline),
      );

      row.append(
        el("span", "past-date mono", shortDate(issue.date)),
        el("span", "past-kind", issue.kindLabel),
        head,
      );
      list.append(row);
    }
  }

  function renderIssue(issue, { scroll = false, replaceHash = true } = {}) {
    const parts = dateParts(issue.date);
    paper.classList.remove("is-empty");
    $("margin").hidden = false;
    $("text").hidden = false;
    $("empty-state").hidden = true;
    paper.setAttribute("aria-busy", "false");

    text($("issue-no"), `第 ${pad(issue.issue, 3)} 期`);
    text($("month"), parts.month);
    text($("day"), parts.day);
    text($("weekday"), parts.weekday);
    const chapterLabel = issue.chapter.label || `第${issue.chapter.index}章`;
    text($("chapter-label"), chapterLabel);

    text($("kicker"), [issue.kindLabel, issue.subject, `${issue.book.author}《${issue.book.title}》`].join(" · "));
    text($("headline"), issue.headline);

    const body = $("body");
    body.replaceChildren();
    for (const paragraph of issue.body) {
      body.append(el("p", "", paragraph));
    }

    text($("quote-text"), issue.quote.text);
    text($("quote-meta"), `${chapterLabel} · 第 ${issue.quote.line} 行`);

    text($("why-text"), issue.why);
    text(
      $("colophon"),
      [
        issue.chapter.path,
        `${charCount(issue.body)} 字`,
        `查证 ${issue.research.chapters}`,
        `联网 ${issue.research.web} 次`,
      ].join(" · "),
    );

    renderPast(state.feed.issues, issue.date);
    state.activeDate = issue.date;

    if (replaceHash && location.hash.slice(1) !== issue.date) {
      history.replaceState(null, "", `#${issue.date}`);
    }

    animatePaper();
    if (scroll) {
      window.scrollTo({ top: 0, behavior: reduceMotion() ? "auto" : "smooth" });
    }
  }

  function renderEmpty(feed, { error = false } = {}) {
    paper.classList.add("is-empty");
    paper.setAttribute("aria-busy", "false");
    $("margin").hidden = true;
    $("text").hidden = true;
    $("empty-state").hidden = false;
    $("past").hidden = true;
    $("day-note").hidden = true;
    text($("issue-no"), "");
    text($("empty-title"), error ? "日课没有打开" : "还没有日课");
    text(
      $("empty-hint"),
      error ? "检查本地服务，再刷新这一页。" : `把一期 JSON 放进 ${feed.dir}，刷新这一页。`,
    );
    state.activeDate = null;
    animatePaper();
  }

  function route({ scroll = false, replaceHash = true } = {}) {
    if (!state.feed) return;
    const issue = pickIssue(location.hash.slice(1));
    if (!issue) {
      renderEmpty(state.feed);
      return;
    }
    renderIssue(issue, { scroll, replaceHash });
  }

  async function boot() {
    try {
      const response = await fetch("/api/feed", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const feed = await response.json();
      if (!feed || !Array.isArray(feed.issues)) throw new Error("feed");
      state.feed = feed;
      renderNotes(feed);
      if (feed.issues.length === 0) {
        renderEmpty(feed);
        return;
      }
      route({ scroll: false, replaceHash: true });
    } catch (error) {
      renderEmpty({ dir: "日课" }, { error: true });
    }
  }

  window.addEventListener("hashchange", () => {
    route({ scroll: true, replaceHash: true });
  });

  boot();
})();
