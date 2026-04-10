/* ============================================
   OMNIBRIDGE — main.ts (TypeScript Version)
   Handles all UI interactions, auth, chat, file
   handling and overlay management.
   ============================================ */

"use strict";

import "./styles.css";

/* ----- Types ----- */

interface User {
  displayName?: string;
  email?: string;
  provider?: string;
}

interface AuthStatus {
  isAuthenticated: boolean;
  user?: User;
  guestMessagesRemaining: number;
  guestMessageLimit: number;
  googleLoginAvailable: boolean;
  uploadsAllowed: boolean;
  as_dict?: () => any;
}

interface ChatState {
  selectedFiles: File[];
  uploadUrls: Map<File, string>;
  auth: AuthStatus | null;
  isBusy: boolean;
}

interface MessageOptions {
  role: "user" | "assistant" | "system";
  text?: string;
  attachments?: { name: string; previewUrl: string }[];
  isTyping?: boolean;
  label?: string;
  thinkingMsg?: string;
}

declare global {
  interface Window {
    APP_CONFIG: any;
  }
}

/* ----- State ----- */
const state: ChatState = {
  selectedFiles: [],
  uploadUrls: new Map(),
  auth: null,
  isBusy: false,
};

/* ----- Element refs ----- */
const el = {
  /* Welcome overlay */
  welcomeOverlay:        document.getElementById("welcome-overlay") as HTMLElement,
  welcomeStartBtn:       document.getElementById("welcome-start-btn") as HTMLButtonElement,

  /* Expired overlay */
  expiredOverlay:        document.getElementById("expired-overlay") as HTMLElement,
  expiredGoogleBtn:      document.getElementById("expired-google-btn") as HTMLAnchorElement,
  expiredEmailBtn:       document.getElementById("expired-email-btn") as HTMLButtonElement,
  expiredCloseBtn:       document.getElementById("expired-close-btn") as HTMLButtonElement,

  /* Sidebar */
  sidebar:               document.getElementById("sidebar") as HTMLElement,
  sidebarToggle:         document.getElementById("sidebar-toggle") as HTMLButtonElement,
  newChatBtn:            document.getElementById("new-chat-btn") as HTMLButtonElement,
  statusDot:             document.getElementById("status-dot") as HTMLElement,
  statusText:            document.getElementById("status-text") as HTMLElement,
  statusMeta:            document.getElementById("status-meta") as HTMLElement,
  mobileStatusDot:       document.getElementById("mobile-status-dot") as HTMLElement,

  /* Auth panel */
  authAvatar:            document.getElementById("auth-avatar") as HTMLElement,
  authTitle:             document.getElementById("auth-title") as HTMLElement,
  authPanelSub:          document.getElementById("auth-panel-sub") as HTMLElement,
  authChips:             document.getElementById("auth-chips") as HTMLElement,
  authActionsWrap:       document.getElementById("auth-actions-wrap") as HTMLElement,
  googleLoginButton:     document.getElementById("google-login-button") as HTMLAnchorElement,
  toggleAuthFormButton:  document.getElementById("toggle-auth-form-button") as HTMLButtonElement,
  logoutButton:          document.getElementById("logout-button") as HTMLButtonElement,
  authForm:              document.getElementById("auth-form") as HTMLElement,
  authDisplayName:       document.getElementById("auth-display-name") as HTMLInputElement,
  authEmail:             document.getElementById("auth-email") as HTMLInputElement,
  authPassword:          document.getElementById("auth-password") as HTMLInputElement,
  authSignupButton:      document.getElementById("auth-signup-button") as HTMLButtonElement,
  authLoginButton:       document.getElementById("auth-login-button") as HTMLButtonElement,
  authFeedback:          document.getElementById("auth-feedback") as HTMLElement,

  /* Chat area */
  messages:              document.getElementById("messages") as HTMLElement,
  heroCard:              document.getElementById("hero-card") as HTMLElement | null,

  /* Composer */
  chatForm:              document.getElementById("chat-form") as HTMLFormElement,
  messageInput:          document.getElementById("message-input") as HTMLTextAreaElement,
  fileInput:             document.getElementById("file-input") as HTMLInputElement,
  pickFilesButton:       document.getElementById("pick-files-button") as HTMLButtonElement,
  selectedFilesEl:       document.getElementById("selected-files") as HTMLElement,
  dropzone:              document.getElementById("dropzone") as HTMLElement,
  dropzoneHint:          document.getElementById("dropzone-hint") as HTMLElement,
  privateModeToggle:     document.getElementById("private-mode-toggle") as HTMLInputElement,
  composerHint:          document.getElementById("composer-hint") as HTMLElement,
  sendButton:            document.getElementById("send-button") as HTMLButtonElement,
  resetButton:           document.getElementById("reset-button") as HTMLButtonElement,
};

/* ============================================
   UTILITIES
   ============================================ */
function formatTime(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatBytes(size: number): string {
  if (!size && size !== 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function createObjectUrl(file: File): string {
  if (!state.uploadUrls.has(file)) {
    state.uploadUrls.set(file, URL.createObjectURL(file));
  }
  return state.uploadUrls.get(file)!;
}

function revokeObjectUrl(file: File): void {
  const url = state.uploadUrls.get(file);
  if (url) { URL.revokeObjectURL(url); state.uploadUrls.delete(file); }
}

function clearAllObjectUrls(): void {
  state.uploadUrls.forEach((url) => URL.revokeObjectURL(url));
  state.uploadUrls.clear();
}

function fileTypeLabel(file: File): string {
  if (file.type.startsWith("image/")) return "IMG";
  const ext = file.name.split(".").pop();
  return ext ? ext.slice(0, 4).toUpperCase() : "FILE";
}

/* ============================================
   MARKDOWN RENDERER
   Converts common markdown into clean HTML.
   No external library needed.
   ============================================ */
function renderMarkdown(text: string): string {
  if (!text) return "";

  // Escape raw HTML to prevent XSS
  const escape = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const lines = text.split("\n");
  const output: string[] = [];
  let inOrderedList  = false;
  let inUnorderedList = false;

  const closeOrderedList = () => { if (inOrderedList)  { output.push("</ol>"); inOrderedList = false; } };
  const closeUnorderedList = () => { if (inUnorderedList) { output.push("</ul>"); inUnorderedList = false; } };
  const closeLists = () => { closeOrderedList(); closeUnorderedList(); };

  // Inline styles: bold, italic, inline-code
  const inlineFormat = (s: string) => {
    return escape(s)
      .replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>")
      .replace(/\*\*(.+?)\*\*/g,   "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g,       "<em>$1</em>")
      .replace(/`([^`]+)`/g,       "<code class=\"inline-code\">$1</code>");
  };

  for (let i = 0; i < lines.length; i++) {
    const raw  = lines[i];
    const line = raw.trimEnd();
    const trimmed = line.trimStart();

    // Heading 1-3
    const h3 = trimmed.match(/^###\s+(.+)/);
    const h2 = trimmed.match(/^##\s+(.+)/);
    const h1 = trimmed.match(/^#\s+(.+)/);
    if (h1 || h2 || h3) {
      closeLists();
      const level = h1 ? 1 : h2 ? 2 : 3;
      const content = (h1 || h2 || h3)![1];
      output.push(`<h${level} class="md-h${level}">${inlineFormat(content)}</h${level}>`);
      continue;
    }

    // Horizontal rule
    if (/^(---+|\*\*\*+|___+)$/.test(trimmed)) {
      closeLists();
      output.push("<hr class=\"md-hr\">");
      continue;
    }

    // Ordered list  ( 1. item )
    const olMatch = trimmed.match(/^(\d+)\.\s+(.+)/);
    if (olMatch) {
      closeUnorderedList();
      if (!inOrderedList) { output.push("<ol class=\"md-ol\">"); inOrderedList = true; }
      output.push(`<li>${inlineFormat(olMatch[2])}</li>`);
      continue;
    }

    // Unordered list  ( * item  or  - item  or  • item )
    const ulMatch = trimmed.match(/^[\*\-•]\s+(.+)/);
    if (ulMatch) {
      closeOrderedList();
      if (!inUnorderedList) { output.push("<ul class=\"md-ul\">"); inUnorderedList = true; }
      output.push(`<li>${inlineFormat(ulMatch[1])}</li>`);
      continue;
    }

    // Blank line
    if (trimmed === "") {
      closeLists();
      output.push("<div class=\"md-spacer\"></div>");
      continue;
    }

    // Normal paragraph line — close any open lists first
    closeLists();
    output.push(`<p class="md-p">${inlineFormat(trimmed)}</p>`);
  }

  closeLists();
  return output.join("\n");
}

/* ============================================
   WELCOME OVERLAY AND INTRO CANVAS
   ============================================ */
let welcomeAnimationId: number;
function initWelcomeCanvas(): void {
  const canvas = document.getElementById("welcome-canvas") as HTMLCanvasElement;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  
  let width = canvas.width = window.innerWidth;
  let height = canvas.height = window.innerHeight;
  
  window.addEventListener("resize", () => {
    width = canvas.width = window.innerWidth;
    height = canvas.height = window.innerHeight;
  });

  const mouse = { x: width / 2, y: height / 2 };
  
  window.addEventListener("mousemove", (e: MouseEvent) => {
    mouse.x = e.x;
    mouse.y = e.y;
  });

  class Square {
    x: number;
    y: number;
    size: number;
    baseX: number;
    baseY: number;
    density: number;
    isCyan: boolean;

    constructor() {
      this.x = Math.random() * width;
      this.y = Math.random() * height;
      this.size = Math.random() * 8 + 4;
      this.baseX = this.x;
      this.baseY = this.y;
      this.density = (Math.random() * 30) + 1;
      this.isCyan = Math.random() > 0.5;
    }
    draw() {
      if (!ctx) return;
      ctx.fillStyle = this.isCyan ? "rgba(56,189,248,0.7)" : "rgba(168,85,247,0.7)";
      ctx.shadowBlur = 15;
      ctx.shadowColor = this.isCyan ? "rgba(56,189,248,0.9)" : "rgba(168,85,247,0.9)";
      ctx.fillRect(this.x, this.y, this.size, this.size);
    }
    update() {
      // Repel from mouse magnetic effect
      let dx = mouse.x - this.x;
      let dy = mouse.y - this.y;
      let distance = Math.sqrt(dx * dx + dy * dy);
      let forceDirectionX = dx / distance;
      let forceDirectionY = dy / distance;
      let maxDistance = 200;
      let force = (maxDistance - distance) / maxDistance;
      let directionX = forceDirectionX * force * this.density;
      let directionY = forceDirectionY * force * this.density;
      
      if (distance < maxDistance) {
        this.x -= directionX;
        this.y -= directionY;
      } else {
        if (this.x !== this.baseX) {
          let dx = this.x - this.baseX;
          this.x -= dx / 20;
        }
        if (this.y !== this.baseY) {
          let dy = this.y - this.baseY;
          this.y -= dy / 20;
        }
      }
      
      // Add slight constant organic drift to base position
      this.baseX += Math.sin(Date.now() * 0.001 + this.density) * 0.3;
      this.baseY += Math.cos(Date.now() * 0.001 + this.density) * 0.3;
      
      this.draw();
    }
  }

  const squaresArray = Array.from({ length: 70 }, () => new Square());

  function animate() {
    if (!ctx) return;
    ctx.clearRect(0, 0, width, height);
    squaresArray.forEach(sq => sq.update());
    welcomeAnimationId = requestAnimationFrame(animate);
  }
  animate();
}

function stopWelcomeCanvas(): void {
  if (welcomeAnimationId) cancelAnimationFrame(welcomeAnimationId);
}

function showWelcome(): void {
  el.welcomeOverlay.classList.remove("hidden", "hiding");
  initWelcomeCanvas();
}

function dismissWelcome(): void {
  el.welcomeOverlay.classList.add("hiding");
  setTimeout(() => {
    el.welcomeOverlay.classList.add("hidden");
    stopWelcomeCanvas();
    el.messageInput.focus();
  }, 420);
}

el.welcomeStartBtn.addEventListener("click", dismissWelcome);

// Dismiss on backdrop click
el.welcomeOverlay.addEventListener("click", (e: MouseEvent) => {
  if (e.target === el.welcomeOverlay || (e.target as HTMLElement).classList.contains("welcome-backdrop")) {
    dismissWelcome();
  }
});

/* ============================================
   EXPIRED SESSION OVERLAY
   ============================================ */
function showExpiredOverlay(): void {
  el.expiredOverlay.removeAttribute("hidden");
  // Configure google button active state
  if (state.auth && !state.auth.googleLoginAvailable) {
    el.expiredGoogleBtn.style.pointerEvents = "none";
    el.expiredGoogleBtn.style.opacity = "0.4";
  }
}

function hideExpiredOverlay(): void {
  el.expiredOverlay.setAttribute("hidden", "");
}

el.expiredCloseBtn.addEventListener("click", hideExpiredOverlay);

el.expiredEmailBtn.addEventListener("click", () => {
  hideExpiredOverlay();
  // Open auth form in sidebar
  el.authForm.hidden = false;
  el.authEmail.focus();
  // On mobile, open sidebar too
  if (window.innerWidth <= 900) {
    el.sidebar.classList.add("is-open");
    ensureSidebarOverlay();
  }
});

// Backdrop click to close
el.expiredOverlay.addEventListener("click", (e: MouseEvent) => {
  if (e.target === el.expiredOverlay || (e.target as HTMLElement).classList.contains("expired-backdrop")) {
    hideExpiredOverlay();
  }
});

/* ============================================
   SIDEBAR (MOBILE TOGGLE)
   ============================================ */
let sidebarOverlay: HTMLElement | null = null;

function ensureSidebarOverlay(): void {
  if (!sidebarOverlay) {
    sidebarOverlay = document.createElement("div");
    sidebarOverlay.className = "sidebar-overlay";
    document.body.appendChild(sidebarOverlay);
    sidebarOverlay.addEventListener("click", closeSidebar);
  }
  sidebarOverlay.classList.add("is-visible");
}

function closeSidebar(): void {
  el.sidebar.classList.remove("is-open");
  if (sidebarOverlay) sidebarOverlay.classList.remove("is-visible");
}

el.sidebarToggle?.addEventListener("click", () => {
  if (el.sidebar.classList.contains("is-open")) {
    closeSidebar();
  } else {
    el.sidebar.classList.add("is-open");
    ensureSidebarOverlay();
  }
});

/* ============================================
   AUTO-RESIZE TEXTAREA
   ============================================ */
el.messageInput.addEventListener("input", () => {
  el.messageInput.style.height = "auto";
  el.messageInput.style.height = `${Math.min(el.messageInput.scrollHeight, 200)}px`;
});

/* ============================================
   AUTH UI
   ============================================ */
function setAuthFeedback(message: string = "", isError: boolean = false): void {
  el.authFeedback.textContent = message;
  el.authFeedback.classList.toggle("is-error", isError);
  el.authFeedback.classList.toggle("is-success", Boolean(message) && !isError);
}

function authChip(label: string, tone: string = ""): string {
  return `<span class="auth-chip ${tone}">${label}</span>`;
}

function updateAuthUi(auth: AuthStatus): void {
  state.auth = auth;
  if (!auth) return;

  const chips: string[] = [];

  if (auth.isAuthenticated && auth.user) {
    const name = auth.user.displayName || auth.user.email || "Signed in";
    const initials = name.charAt(0).toUpperCase();

    el.authAvatar.textContent = initials;
    el.authTitle.textContent = name;
    el.authPanelSub.textContent = auth.user.email || "Full access";

    el.googleLoginButton.hidden = true;
    el.toggleAuthFormButton.hidden = true;
    el.logoutButton.hidden = false;
    el.authForm.hidden = true;

    setAuthFeedback("");
    chips.push(authChip("✓ Full access", "is-success"));
    chips.push(authChip(auth.user.provider === "google" ? "Google" : "Email"));
  } else {
    el.authAvatar.textContent = "G";
    el.authTitle.textContent = "Guest";
    el.authPanelSub.textContent = "Limited access";

    el.googleLoginButton.hidden = false;
    el.toggleAuthFormButton.hidden = false;
    el.logoutButton.hidden = true;

    const remaining = auth.guestMessagesRemaining ?? 0;
    chips.push(authChip(`${remaining} messages left`, remaining <= 2 ? "is-warning" : ""));
    chips.push(authChip("Uploads locked", "is-warning"));

    // Google button state
    if (!auth.googleLoginAvailable) {
      el.googleLoginButton.classList.add("is-disabled");
      el.googleLoginButton.setAttribute("aria-disabled", "true");
      el.googleLoginButton.href = "#";
    } else {
      el.googleLoginButton.classList.remove("is-disabled");
      el.googleLoginButton.setAttribute("aria-disabled", "false");
      el.googleLoginButton.href = "/auth/google/start?next=/";
    }

    // Trigger expired overlay
    if (remaining <= 0 && !auth.isAuthenticated) {
      showExpiredOverlay();
    }
  }

  el.authChips.innerHTML = chips.join("");
  updateDropzoneAccess(auth);
  updateComposerHint(auth);
}

function updateDropzoneAccess(auth: AuthStatus): void {
  const allowed = Boolean(auth?.uploadsAllowed);
  el.dropzone.classList.toggle("is-locked", !allowed);
  el.dropzone.setAttribute("aria-disabled", allowed ? "false" : "true");
  if (el.dropzoneHint) {
    el.dropzoneHint.textContent = allowed
      ? "Screenshots, PDFs, images, CSVs and more"
      : "Sign in to unlock file & image uploads";
  }
  el.pickFilesButton.disabled = !allowed;
}

function updateComposerHint(auth: AuthStatus): void {
  if (!auth) {
    el.composerHint.textContent = "Enter to send · Shift+Enter for new line";
    return;
  }
  if (auth.isAuthenticated) {
    el.composerHint.textContent = "Signed in · File uploads enabled";
    return;
  }
  const rem = auth.guestMessagesRemaining ?? 0;
  el.composerHint.textContent = `${rem}/${auth.guestMessageLimit} guest messages remaining`;
}

/* ============================================
   AUTH FORM TOGGLE
   ============================================ */
el.toggleAuthFormButton.addEventListener("click", () => {
  const hidden = el.authForm.hidden;
  el.authForm.hidden = !hidden;
  if (!hidden) {
    setAuthFeedback("");
  } else {
    el.authEmail.focus();
  }
});

el.googleLoginButton.addEventListener("click", (e: MouseEvent) => {
  if (el.googleLoginButton.classList.contains("is-disabled")) {
    e.preventDefault();
    setAuthFeedback("Google sign-in is not configured on this server.", true);
  }
});

/* ============================================
   AUTH SUBMIT (signup / login)
   ============================================ */
async function submitAuth(mode: "signup" | "login"): Promise<void> {
  const email       = el.authEmail.value.trim();
  const password    = el.authPassword.value;
  const displayName = el.authDisplayName.value.trim();

  setBusy(true);
  setAuthFeedback(mode === "signup" ? "Creating your account…" : "Signing you in…");

  try {
    const res = await fetch(mode === "signup" ? "/api/auth/signup" : "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, displayName }),
    });
    const data = await res.json();

    if (!res.ok || !data.ok) throw new Error(data.error || "Authentication failed.");

    updateAuthUi(data.auth);
    el.authPassword.value = "";
    setAuthFeedback(mode === "signup" ? "Account created! Full access unlocked." : "Signed in successfully.", false);
    appendMessage({
      role: "system",
      text: mode === "signup"
        ? "Welcome! Your account is ready. File uploads and extended conversations are now available."
        : "Signed in. All features are now unlocked.",
    });
  } catch (err: any) {
    setAuthFeedback(err.message, true);
  } finally {
    setBusy(false);
  }
}

el.authSignupButton.addEventListener("click", () => submitAuth("signup"));
el.authLoginButton.addEventListener("click",  () => submitAuth("login"));

/* ============================================
   LOGOUT
   ============================================ */
async function logout(): Promise<void> {
  setBusy(true);
  try {
    const res = await fetch("/api/auth/logout", { method: "POST" });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || "Could not sign out.");
    updateAuthUi(data.auth);
    setAuthFeedback("Signed out. Guest mode active.");
    appendMessage({ role: "system", text: "Signed out successfully. Guest mode is now active." });
  } catch (err: any) {
    setAuthFeedback(err.message, true);
  } finally {
    setBusy(false);
  }
}

el.logoutButton.addEventListener("click", logout);

/* ============================================
   HERO CARD
   ============================================ */
function removeHero(): void {
  if (el.heroCard) {
    el.heroCard.style.animation = "cardSlideOut 0.2s ease forwards";
    setTimeout(() => { el.heroCard?.remove(); el.heroCard = null; }, 200);
  }
}

/* Wire welcome chips in chat */
document.querySelectorAll(".welcome-chip").forEach((btn) => {
  (btn as HTMLElement).addEventListener("click", () => {
    el.messageInput.value = (btn as HTMLElement).dataset.prompt || "";
    el.messageInput.dispatchEvent(new Event("input"));
    el.messageInput.focus();
  });
});

/* ============================================
   MESSAGES
   ============================================ */
function appendMessage({ role, text, attachments = [], isTyping = false, label = "", thinkingMsg = "" }: MessageOptions): HTMLElement {
  removeHero();

  const article = document.createElement("article");
  article.className = `message ${role}`;

  /* Avatar */
  const avatarDiv = document.createElement("div");
  avatarDiv.className = "message-avatar";
  if (role === "user") {
    const initials = (state.auth?.user?.displayName || state.auth?.user?.email || "Y").charAt(0).toUpperCase();
    avatarDiv.textContent = state.auth?.isAuthenticated ? initials : "Y";
  } else if (role === "assistant") {
    avatarDiv.innerHTML = `<svg viewBox="0 0 24 24" fill="none" width="16" height="16"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M8 12c0-2 4-5 8 0s-4 5-8 0z" fill="currentColor" opacity="0.8"/></svg>`;
  } else {
    avatarDiv.textContent = "!";
  }

  /* Content wrapper */
  const contentDiv = document.createElement("div");
  contentDiv.className = "message-content";

  /* Bubble */
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";

  if (isTyping) {
    bubble.innerHTML = `
      <div class="typing-row">
        <div class="typing-dots"><span></span><span></span><span></span></div>
        <span class="thinking-label">${thinkingMsg || "Thinking…"}</span>
      </div>
    `;
  } else {
    if (role === "assistant") {
      bubble.classList.add("md-content");
      bubble.innerHTML = renderMarkdown(text || "");
    } else {
      bubble.textContent = text || "";
    }
  }

  contentDiv.appendChild(bubble);

  /* Attachments */
  if (attachments.length) {
    const attachWrap = document.createElement("div");
    attachWrap.className = "message-attachments";
    attachments.forEach((file) => {
      const chip = document.createElement("div");
      chip.className = "attachment-chip";
      if (file.previewUrl) {
        const img = document.createElement("img");
        img.src = file.previewUrl;
        img.alt = file.name;
        chip.appendChild(img);
      }
      const span = document.createElement("span");
      span.textContent = file.name;
      chip.appendChild(span);
      attachWrap.appendChild(chip);
    });
    contentDiv.appendChild(attachWrap);
  }

  /* Meta row */
  const metaDiv = document.createElement("div");
  metaDiv.className = "message-meta";
  const timeSpan = document.createElement("span");
  timeSpan.className = "message-time";
  timeSpan.textContent = formatTime();
  metaDiv.appendChild(timeSpan);

  /* Source badge — only for assistant, no model name shown */
  if (role === "assistant" && !isTyping) {
    const badge = document.createElement("span");
    badge.className = "message-source-badge";
    badge.textContent = label || "OMNIBRIDGE";
    metaDiv.appendChild(badge);
  }

  contentDiv.appendChild(metaDiv);

  /* Copy button — only for non-typing assistant messages */
  if (role === "assistant" && !isTyping && text) {
    const copyBtn = document.createElement("button");
    copyBtn.className = "copy-btn";
    copyBtn.setAttribute("aria-label", "Copy response");
    copyBtn.title = "Copy";
    copyBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 13 13" fill="none">
      <rect x="4" y="4" width="8" height="8" rx="1.5" stroke="currentColor" stroke-width="1.4"/>
      <path d="M2 9H1.5A1.5 1.5 0 0 1 0 7.5v-6A1.5 1.5 0 0 1 1.5 0h6A1.5 1.5 0 0 1 9 1.5V2" stroke="currentColor" stroke-width="1.4"/>
    </svg>`;
    copyBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(text).then(() => {
        copyBtn.classList.add("copied");
        copyBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 13 13" fill="none">
          <path d="M2 6.5L5 9.5L11 3.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>`;
        setTimeout(() => {
          copyBtn.classList.remove("copied");
          copyBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 13 13" fill="none">
            <rect x="4" y="4" width="8" height="8" rx="1.5" stroke="currentColor" stroke-width="1.4"/>
            <path d="M2 9H1.5A1.5 1.5 0 0 1 0 7.5v-6A1.5 1.5 0 0 1 1.5 0h6A1.5 1.5 0 0 1 9 1.5V2" stroke="currentColor" stroke-width="1.4"/>
          </svg>`;
        }, 2000);
      });
    });
    contentDiv.appendChild(copyBtn);
  }

  article.appendChild(avatarDiv);
  article.appendChild(contentDiv);

  el.messages.appendChild(article);
  el.messages.scrollTop = el.messages.scrollHeight;

  return article;
}

/* ============================================
   BUSY STATE
   ============================================ */
function setBusy(busy: boolean): void {
  state.isBusy = busy;
  el.sendButton.disabled = busy;
  el.resetButton.disabled = busy;
  el.messageInput.disabled = busy;
  el.fileInput.disabled = busy;
  el.privateModeToggle.disabled = busy;
  el.toggleAuthFormButton.disabled = busy;
  el.authSignupButton.disabled = busy;
  el.authLoginButton.disabled = busy;
  el.logoutButton.disabled = busy;
  // Attach button follows upload permission too
  el.pickFilesButton.disabled = busy || !state.auth?.uploadsAllowed;
}

/* ============================================
   FILE HANDLING
   ============================================ */
function renderSelectedFiles(): void {
  el.selectedFilesEl.innerHTML = "";

  state.selectedFiles.forEach((file, index) => {
    const pill = document.createElement("div");
    pill.className = "file-pill";

    let media: HTMLElement;
    if (file.type.startsWith("image/")) {
      media = document.createElement("img");
      (media as HTMLImageElement).className = "file-thumb";
      (media as HTMLImageElement).src = createObjectUrl(file);
      (media as HTMLImageElement).alt = file.name;
    } else {
      media = document.createElement("div");
      media.className = "file-icon";
      media.textContent = fileTypeLabel(file);
    }

    const metaDiv = document.createElement("div");
    metaDiv.className = "file-meta";
    metaDiv.innerHTML = `
      <div class="file-name" title="${file.name}">${file.name}</div>
      <div class="file-size">${formatBytes(file.size)}</div>
    `;

    const removeBtn = document.createElement("button");
    removeBtn.className = "remove-file";
    removeBtn.type = "button";
    removeBtn.textContent = "×";
    removeBtn.setAttribute("aria-label", `Remove ${file.name}`);
    removeBtn.addEventListener("click", () => {
      revokeObjectUrl(file);
      state.selectedFiles.splice(index, 1);
      renderSelectedFiles();
    });

    pill.append(media, metaDiv, removeBtn);
    el.selectedFilesEl.appendChild(pill);
  });
}

function promptForAuth(message: string): void {
  setAuthFeedback(message, true);
  el.authForm.hidden = false;
  el.authEmail.focus();
}

function addFiles(fileList: FileList | null): void {
  if (!fileList) return;
  if (!state.auth?.uploadsAllowed) {
    showExpiredOverlay();
    return;
  }

  Array.from(fileList).forEach((file) => {
    const duplicate = state.selectedFiles.some(
      (f) => f.name === file.name && f.size === file.size && f.lastModified === file.lastModified
    );
    if (!duplicate) state.selectedFiles.push(file);
  });

  renderSelectedFiles();

  // Show dropzone if files selected
  if (state.selectedFiles.length > 0) {
    el.dropzone.classList.add("is-visible");
  }
}

/* ============================================
   DROPZONE
   ============================================ */
function attachDropzoneEvents(): void {
  (["dragenter", "dragover"] as const).forEach((evt) => {
    el.dropzone.addEventListener(evt, ((e: DragEvent) => {
      e.preventDefault();
      if (state.auth?.uploadsAllowed) el.dropzone.classList.add("is-active");
    }) as EventListener);
  });

  (["dragleave", "drop"] as const).forEach((evt) => {
    el.dropzone.addEventListener(evt, ((e: DragEvent) => {
      e.preventDefault();
      el.dropzone.classList.remove("is-active");
    }) as EventListener);
  });

  el.dropzone.addEventListener("drop", (e: DragEvent) => {
    if (!state.auth?.uploadsAllowed) { showExpiredOverlay(); return; }
    addFiles(e.dataTransfer?.files || null);
  });

  el.dropzone.addEventListener("click", () => {
    if (!state.auth?.uploadsAllowed) { showExpiredOverlay(); return; }
    el.fileInput.click();
  });

  el.dropzone.addEventListener("keydown", (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (!state.auth?.uploadsAllowed) { showExpiredOverlay(); return; }
      el.fileInput.click();
    }
  });
}

/* ============================================
   PERSONALITY — Rotating thinking messages
   ============================================ */
const THINKING_MESSAGES = [
  "Thinking…",
  "Analyzing your request…",
  "Processing that for you…",
  "Working on it…",
  "Let me figure that out…",
  "Crafting a response…",
  "On it…",
  "Reading carefully…",
];

let thinkingIndex = 0;
function nextThinkingMessage(): string {
  const msg = THINKING_MESSAGES[thinkingIndex % THINKING_MESSAGES.length];
  thinkingIndex++;
  return msg;
}

/* Flash effect on composer when message is sent */
function flashComposer(): void {
  const form = el.chatForm;
  form.classList.add("send-flash");
  setTimeout(() => form.classList.remove("send-flash"), 400);
}

/* ============================================
   SEND MESSAGE
   ============================================ */
async function sendMessage(event: Event): Promise<void> {
  event.preventDefault();

  const message = el.messageInput.value.trim();
  const auth = state.auth;

  if (!message && !state.selectedFiles.length) return;

  if (state.selectedFiles.length && !auth?.uploadsAllowed) {
    showExpiredOverlay();
    return;
  }

  if (!auth?.isAuthenticated && auth && auth.guestMessagesRemaining <= 0) {
    showExpiredOverlay();
    return;
  }

  const filesForBubble = state.selectedFiles.map((file) => ({
    name: file.name,
    previewUrl: file.type.startsWith("image/") ? createObjectUrl(file) : "",
  }));

  appendMessage({ role: "user", text: message, attachments: filesForBubble });
  flashComposer();

  const typingBubble = appendMessage({ role: "assistant", isTyping: true, thinkingMsg: nextThinkingMessage() });

  const payload = new FormData();
  payload.append("message", message);
  payload.append("private_mode", el.privateModeToggle.checked ? "true" : "false");
  state.selectedFiles.forEach((file) => payload.append("files", file));

  state.selectedFiles = [];
  renderSelectedFiles();
  el.dropzone.classList.remove("is-visible");
  el.messageInput.value = "";
  el.messageInput.style.height = "auto";
  setBusy(true);

  try {
    const res = await fetch("/api/chat", { method: "POST", body: payload });
    const data = await res.json();
    typingBubble.remove();

    if (data.auth) updateAuthUi(data.auth);

    if (!res.ok || !data.ok) {
      if (data.authRequired) {
        showExpiredOverlay();
        appendMessage({ role: "system", text: data.error || "Please sign in to continue." });
        return;
      }
      throw new Error(data.error || "Something went wrong. Please try again.");
    }

    // Strip model name from label — always show "OMNIBRIDGE"
    appendMessage({
      role: "assistant",
      text: data.assistantMessage,
      label: "OMNIBRIDGE",
    });
  } catch (err: any) {
    typingBubble.remove();
    appendMessage({ role: "system", text: err.message });
  } finally {
    setBusy(false);
    el.messageInput.focus();
  }
}

/* ============================================
   RESET CONVERSATION
   ============================================ */
async function resetConversation(): Promise<void> {
  setBusy(true);
  try {
    const res = await fetch("/api/reset", { method: "POST" });
    const data = await res.json();
    if (data.auth) updateAuthUi(data.auth);

    clearAllObjectUrls();
    state.selectedFiles = [];
    renderSelectedFiles();
    el.dropzone.classList.remove("is-visible");

    // Restore hero card
    el.messages.innerHTML = `
      <div class="chat-welcome" id="hero-card">
        <div class="chat-welcome-logo">
          <svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="32" cy="32" r="26" stroke="url(#rwlg1)" stroke-width="2.5"/>
            <path d="M18 32 C18 23 32 13 46 32 C32 51 18 41 18 32Z" fill="url(#rwlg2)" opacity="0.9"/>
            <circle cx="32" cy="32" r="5.5" fill="white" opacity="0.95"/>
            <defs>
              <linearGradient id="rwlg1" x1="6" y1="6" x2="58" y2="58">
                <stop offset="0%" stop-color="#7c6dff"/>
                <stop offset="100%" stop-color="#38bdf8"/>
              </linearGradient>
              <linearGradient id="rwlg2" x1="18" y1="13" x2="46" y2="51">
                <stop offset="0%" stop-color="#7c6dff"/>
                <stop offset="100%" stop-color="#38bdf8"/>
              </linearGradient>
            </defs>
          </svg>
        </div>
        <h2 class="chat-welcome-title">Session reset. Ready when you are.</h2>
        <p class="chat-welcome-sub">Your conversation history has been cleared. Start a fresh conversation below.</p>
      </div>
    `;
    el.heroCard = document.getElementById("hero-card") as HTMLElement;
  } finally {
    setBusy(false);
  }
}

/* ============================================
   STATUS / HEALTH CHECK
   ============================================ */
async function loadStatus(): Promise<void> {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();

    if (!res.ok || !data.ok) throw new Error(data.error || "Backend unavailable.");

    setStatusOnline();
    el.statusText.textContent = "All systems operational";

    // Build a clean status description without mentioning model names
    const parts: string[] = [];
    if (data.system?.availableRamGb) parts.push(`${data.system.availableRamGb} GiB RAM`);
    if (data.system?.cpuThreads) parts.push(`${data.system.cpuThreads} CPU threads`);
    el.statusMeta.textContent = parts.length ? parts.join(" · ") : "Ready";

    if (data.auth) updateAuthUi(data.auth);
  } catch (err: any) {
    setStatusError();
    el.statusText.textContent = "Connection issue";
    el.statusMeta.textContent = err.message;
  }
}

function setStatusOnline(): void {
  el.statusDot.classList.remove("error");
  el.statusDot.classList.add("online");
  el.mobileStatusDot?.classList.add("online");
}

function setStatusError(): void {
  el.statusDot.classList.remove("online");
  el.statusDot.classList.add("error");
  el.mobileStatusDot?.classList.remove("online");
}

async function loadAuthStatus(): Promise<void> {
  try {
    const res = await fetch("/api/auth/status");
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || "Could not load auth status.");
    updateAuthUi(data.auth);
  } catch (err: any) {
    setAuthFeedback(err.message, true);
  }
}

/* ============================================
   GOOGLE AUTH REDIRECT HANDLER
   ============================================ */
function handleAuthQueryParams(): void {
  const params = new URLSearchParams(window.location.search);
  const success = params.get("auth");
  const error   = params.get("auth_error");

  if (!success && !error) return;

  if (success === "google-success") {
    appendMessage({ role: "system", text: "Google sign-in successful. All features are now unlocked." });
  }

  if (error) {
    appendMessage({ role: "system", text: decodeURIComponent(error) });
    promptForAuth(decodeURIComponent(error));
  }

  window.history.replaceState({}, document.title, window.location.pathname);
}

async function loadHistory(): Promise<void> {
  if (!state.auth?.isAuthenticated) return;
  try {
    const res = await fetch("/api/history");
    if (!res.ok) return;
    const data = await res.json();
    if (data.ok && data.messages && data.messages.length > 0) {
      if (document.getElementById("hero-card")) {
        document.getElementById("hero-card")!.style.display = "none";
      }
      for (const msg of data.messages) {
        appendMessage({
          role: msg.role === "assistant" ? "assistant" : "user",
          text: msg.content
        });
      }
    }
  } catch (err) {
    console.error("Failed to load chat history", err);
  }
}

/* ============================================
   WIRE UP EVENTS
   ============================================ */
function attachComposerEvents(): void {
  el.pickFilesButton.addEventListener("click", () => {
    if (!state.auth?.uploadsAllowed) { showExpiredOverlay(); return; }
    el.fileInput.click();
  });

  el.fileInput.addEventListener("change", (e: Event) => {
    addFiles((e.target as HTMLInputElement).files);
    el.fileInput.value = "";
  });

  el.chatForm.addEventListener("submit", sendMessage);

  el.messageInput.addEventListener("keydown", (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      el.chatForm.requestSubmit();
    }
  });

  el.resetButton.addEventListener("click", resetConversation);

  el.newChatBtn?.addEventListener("click", resetConversation);
}

function attachPromptButtons(): void {
  document.querySelectorAll(".quick-prompt-btn").forEach((btn) => {
    (btn as HTMLElement).addEventListener("click", () => {
      el.messageInput.value = (btn as HTMLElement).dataset.prompt || "";
      el.messageInput.dispatchEvent(new Event("input"));
      el.messageInput.focus();
      // Close sidebar on mobile after picking prompt
      if (window.innerWidth <= 900) closeSidebar();
    });
  });
}

/* ============================================
   INIT
   ============================================ */
// INIT
window.addEventListener("beforeunload", clearAllObjectUrls);

// Load status & auth, then wire up everything
Promise.all([loadStatus(), loadAuthStatus()]).finally(() => {
  handleAuthQueryParams();
  attachDropzoneEvents();
  attachPromptButtons();
  attachComposerEvents();

  // Only show welcome overlay if guest and no auth params (to avoid re-flashing)
  const params = new URLSearchParams(window.location.search);
  const isAuthAction = params.has("auth") || params.has("auth_error");
  
  if (!state.auth?.isAuthenticated && !isAuthAction) {
    showWelcome();
  }

  // Load history if authenticated
  if (state.auth?.isAuthenticated) {
    loadHistory();
  }
});
