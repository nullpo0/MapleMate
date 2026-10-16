"""Readable native settings; explicit, character-scoped state reset."""
from datetime import datetime
import os
import threading
import tkinter as tk
from tkinter import ttk
from PIL import ImageTk
from . import __version__
from .clients import ClientSetupError, ClaudeDesktopSetup, CodexSetup, install_maplemate_skill
from .secrets import key_configured
from .setup import configure_key
from .capture import CaptureError

BG = "#F4F6F1"
CARD = "#FFFFFF"
INK = "#20372B"
MUTED = "#586A5F"
GREEN = "#214D3B"
BORDER = "#DCE4DA"
FONT = "맑은 고딕"


def meso_text(value):
    if value is None:
        return "미설정"
    if value >= 100_000_000:
        return f"{value / 100_000_000:,.2f}".rstrip("0").rstrip(".") + "억 메소"
    return f"{value:,} 메소"


def time_text(value):
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%m월 %d일 %H:%M")
    except (ValueError, TypeError):
        return "확인 시각 없음"


class SettingsWindow:
    def __init__(self, root, config, store, manager, post, message, brand_image):
        self.config, self.store, self.manager = config, store, manager
        self.post, self.message = post, message
        self.window = tk.Toplevel(root)
        self.window.title("MapleMate")
        self.window.configure(bg=BG)
        self.window.protocol("WM_DELETE_WINDOW", self.window.withdraw)
        self.scale = max(1, float(root.tk.call("tk", "scaling")) / (96 / 72))
        width = min(self.px(490), root.winfo_screenwidth() - 80)
        height = min(self.px(410), root.winfo_screenheight() - 100)
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(self.px(440), width), min(self.px(360), height))
        self.controls, self.navigation = [], {}
        self.busy, self.page, self.selected_character = False, "connection", ""
        self.image = ImageTk.PhotoImage(brand_image.resize((self.px(24), self.px(24))))
        self.window.iconphoto(False, self.image)
        self.style = ttk.Style(self.window)
        self.style.theme_use("clam")
        self.style.configure("Maple.TEntry", font=(FONT, 10), fieldbackground=CARD, foreground=INK,
                             bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=5)
        self.style.map("Maple.TEntry", bordercolor=[("focus", GREEN)])
        self.style.configure("Maple.TCombobox", font=(FONT, 10), fieldbackground=CARD, foreground=INK, padding=4)
        self.style.map("Maple.TCombobox", fieldbackground=[("readonly", CARD)], selectbackground=[("readonly", CARD)],
                       selectforeground=[("readonly", INK)])
        self.window.option_add("*TCombobox*Listbox.font", (FONT, 10))
        self.window.option_add("*TCombobox*Listbox.background", CARD)
        self.window.option_add("*TCombobox*Listbox.foreground", INK)
        self.style.configure("Maple.TRadiobutton", background=CARD, foreground=INK, font=(FONT, 10))
        self._build_shell()
        self.render("connection")

    def px(self, value):
        return round(value * self.scale)

    def label(self, parent, text="", size=10, color=INK, bold=False, bg=None, wrap=False, **kwargs):
        widget = tk.Label(parent, text=text, bg=bg or parent.cget("bg"), fg=color,
                          font=(FONT, size, "bold" if bold else "normal"), anchor="w", justify="left", **kwargs)
        if wrap:
            widget.configure(wraplength=self.px(640))
            widget.bind("<Configure>", lambda event: widget.configure(wraplength=max(1, event.width - 4)))
        return widget

    def button(self, parent, text, command, kind="primary"):
        colors = {"primary": (GREEN, CARD, "#173C2D"), "secondary": ("#EAF0E7", GREEN, "#DEE8D9"),
                  "danger": ("#FAEAE6", "#92352C", "#F5D8D0")}
        background, foreground, active = colors[kind]
        widget = tk.Button(parent, text=text, command=command, bg=background, fg=foreground,
                           activebackground=active, activeforeground=foreground, disabledforeground="#91A197",
                           font=(FONT, 9, "bold"), relief="flat", bd=0, padx=self.px(10), pady=self.px(6), cursor="hand2")
        self.controls.append(widget)
        return widget

    def card(self, parent, title=None, subtitle=None):
        widget = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1,
                          padx=self.px(12), pady=self.px(10))
        if title:
            self.label(widget, title, 10, bold=True).pack(anchor="w")
        if subtitle:
            self.label(widget, subtitle, color=MUTED, wrap=True).pack(anchor="w", fill="x", pady=(5, 12))
        return widget

    def _build_shell(self):
        header = tk.Frame(self.window, bg=GREEN, padx=self.px(14), pady=self.px(9))
        header.pack(fill="x")
        tk.Label(header, image=self.image, bg=GREEN).pack(side="left")
        self.label(header, "MapleMate", 12, CARD, True).pack(side="left", padx=self.px(8))
        self.label(header, __version__, 9, "#C3D5C8").pack(side="right")
        tabs = tk.Frame(self.window, bg=BG, padx=self.px(12), pady=self.px(8))
        tabs.pack(fill="x")
        for key, text in (("connection", "연결"), ("records", "캐릭터"), ("help", "데이터")):
            button = self.button(tabs, text, lambda page=key: self.render(page), "secondary")
            button.pack(side="left", fill="x", expand=True, padx=3)
            self.navigation[key] = button
        footer = tk.Frame(self.window, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        footer.pack(side="bottom", fill="x")
        self.status_label = self.label(footer, size=9, color=MUTED, wrap=True, textvariable=self.message, height=2)
        self.status_label.pack(fill="x", padx=self.px(14), pady=self.px(3))
        self.canvas = tk.Canvas(self.window, bg=BG, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.window, orient="vertical", command=self.canvas.yview)
        self._scrollable, self._scroll_job = False, None
        self.canvas.pack(fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.body = tk.Frame(self.canvas, bg=BG, padx=self.px(15), pady=self.px(3))
        self.body_window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._schedule_scroll)
        self.canvas.bind("<Configure>", self._fit_width)
        self.window.bind("<MouseWheel>", self._wheel)

    def _fit_width(self, event):
        self.canvas.itemconfigure(self.body_window, width=event.width)
        self._schedule_scroll()

    def _schedule_scroll(self, event=None):
        if self._scroll_job is None:
            self._scroll_job = self.window.after_idle(self._sync_scroll)

    def _sync_scroll(self):
        self._scroll_job = None
        height = max(1, self.canvas.winfo_height())
        content_height = self.body.winfo_reqheight()
        self._scrollable = content_height > height
        if self._scrollable:
            if not self.scrollbar.winfo_manager():
                self.scrollbar.pack(side="right", fill="y", before=self.canvas)
        else:
            self.scrollbar.pack_forget()
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), max(height, content_height)))
        if not self._scrollable:
            self.canvas.yview_moveto(0)

    def _wheel(self, event):
        if self._scrollable and (str(event.widget).startswith(str(self.body)) or event.widget == self.canvas):
            self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def show(self):
        self.window.deiconify()
        self.window.lift()
        if not self.busy:
            self.render(self.page)

    def render(self, page):
        if self.busy:
            return
        self.page = page
        self.controls = list(self.navigation.values())
        for child in self.body.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)
        for name, button in self.navigation.items():
            button.configure(bg=GREEN if name == page else "#EAF0E7", fg=CARD if name == page else GREEN)
        {"connection": self._connection, "records": self._records, "help": self._help}[page]()
        self.window.update_idletasks()
        self._fit_width(type("Width", (), {"width": self.canvas.winfo_width()})())


    def connection_status(self, client):
        try:
            status = client(self.config).status()
        except ClientSetupError:
            return "확인 필요"
        return {"registered": "등록됨", "not_registered": "미등록", "disabled": "활성화 필요", "different_settings": "다시 등록 필요"}.get(status, "확인 필요")

    def _connection(self):
        configured = key_configured(self.config.data_dir)
        api = self.card(self.body, "Nexon API  ·  " + ("저장됨" if configured else "미설정"))
        api.pack(fill="x", pady=(0, self.px(8)))
        row = tk.Frame(api, bg=CARD)
        row.pack(fill="x", pady=(6, 5))
        self.button(row, "저장", self._save_key).pack(side="right", padx=(self.px(8), 0))
        self.key_entry = ttk.Entry(row, show="•", style="Maple.TEntry", font=(FONT, 10))
        self.key_entry.pack(side="left", fill="x", expand=True)
        ai = self.card(self.body)
        ai.pack(fill="x")
        for index, (name, client, operation) in enumerate((("Codex", CodexSetup, self._connect_codex),
                                                        ("Claude Desktop", ClaudeDesktopSetup, self._connect_claude))):
            row = tk.Frame(ai, bg=CARD)
            row.pack(fill="x", pady=(0 if index == 0 else self.px(8), 0))
            self.label(row, name, 10, bold=True).pack(side="left")
            self.button(row, "연결", lambda action=operation: self.task(action), "secondary").pack(side="right")
            self.label(row, self.connection_status(client), 9, MUTED).pack(side="right", padx=self.px(10))

    def _save_key(self):
        private_key = self.key_entry.get().strip()
        if not private_key:
            self.message.set("새 API 키를 입력하세요.")
            self.key_entry.focus_set()
            return
        self.key_entry.delete(0, "end")
        def save():
            count = configure_key(self.config, private_key)
            return f"API 키 저장 완료 · 캐릭터 {count}개"
        self.task(save)

    def _connect_codex(self):
        status = CodexSetup(self.config).install()
        try:
            install_maplemate_skill()
        except ClientSetupError as error:
            return "Codex 연결 설정 완료. " + str(error)
        return "Codex 설정 등록 완료 · 앱을 다시 시작하세요." if status == "registered" else "Codex에서 MapleMate MCP를 활성화하세요."

    def _connect_claude(self):
        ClaudeDesktopSetup(self.config).install()
        return "Claude 설정 등록 완료 · 완전히 종료 후 다시 실행하세요."

    def _records(self):
        names = self.store.character_names()
        if not names:
            self.label(self.body, "등록된 캐릭터 없음", color=MUTED).pack(anchor="w", pady=12)
            return
        if self.selected_character not in names:
            active = self.store.snapshot().get("character_name")
            self.selected_character = active if active in names else names[0]
        choose = tk.Frame(self.body, bg=BG)
        choose.pack(fill="x", pady=(0, self.px(8)))
        self.button(choose, "새로고침", lambda: self.render("records"), "secondary").pack(side="right", padx=(self.px(8), 0))
        self.character_choice = ttk.Combobox(choose, values=names, state="readonly", style="Maple.TCombobox", font=(FONT, 10))
        self.character_choice.set(self.selected_character)
        self.character_choice.pack(side="left", fill="x", expand=True)
        def selected(_):
            self.selected_character = self.character_choice.get()
            self.render("records")
        self.character_choice.bind("<<ComboboxSelected>>", selected)
        state = self.store.snapshot(self.selected_character)
        context = state["user_context"]
        card = self.card(self.body)
        card.pack(fill="x", pady=(0, self.px(8)))
        play_time = (f"하루 {context['daily_play_minutes']}분" if context.get("daily_play_minutes") is not None
                     else f"주 {context['weekly_play_minutes']}분" if context.get("weekly_play_minutes") is not None else "—")
        for title, value in (("목표", context.get("goal") or context.get("target_content") or "—"),
                             ("예산", meso_text(context.get("budget_meso"))), ("시간", play_time),
                             ("화면 관찰", time_text((state.get("screen_observation") or {}).get("observed_at")))):
            row = tk.Frame(card, bg=CARD)
            row.pack(fill="x", pady=2)
            self.label(row, title, 9, MUTED, width=9).pack(side="left", anchor="n")
            self.label(row, str(value)[:240] + ("…" if len(str(value)) > 240 else ""), 9, wrap=True).pack(side="left", fill="x", expand=True)
        reset = self.card(self.body)
        reset.pack(fill="x")
        self.reset_scope = tk.StringVar(value="context")
        self.button(reset, "초기화", self._confirm_reset, "danger").pack(side="right", padx=(8, 0))
        for value, title in (("context", "사용자 설정"), ("all", "설정 + 관찰 기록")):
            ttk.Radiobutton(reset, text=title, variable=self.reset_scope, value=value, style="Maple.TRadiobutton").pack(anchor="w", pady=2)

    def _confirm_reset(self):
        name, scope = self.selected_character, self.reset_scope.get()
        revision = self.store.snapshot(name)["revision"]
        dialog = tk.Toplevel(self.window)
        dialog.title("캐릭터 기록 초기화")
        dialog.iconphoto(False, self.image)
        dialog.configure(bg=CARD)
        dialog.transient(self.window)
        dialog.resizable(False, False)
        width = min(self.px(430), self.window.winfo_width() - 30)
        frame = tk.Frame(dialog, bg=CARD, padx=self.px(18), pady=self.px(16))
        frame.pack(fill="both", expand=True)
        self.label(frame, name + " · 기록 초기화", 12, bold=True).pack(anchor="w")
        detail = "목표·예산·시간·보스 경험과 성장 판단" if scope == "context" else "사용자 설정, 메소·장비 화면 판독, 저장된 API 관찰"
        self.label(frame, detail, color=MUTED, wraplength=width - self.px(56)).pack(anchor="w", fill="x", pady=(14, 8))
        self.label(frame, "API 키·연결 설정은 유지됩니다. 삭제 후 복구할 수 없습니다.", color="#92352C", wraplength=width - self.px(56)).pack(anchor="w", fill="x", pady=(0, 18))
        row = tk.Frame(frame, bg=CARD)
        row.pack(fill="x")
        def reset():
            dialog.destroy()
            def operation():
                try:
                    self.manager.reset_player_state(name, scope, revision)
                except ValueError:
                    raise ClientSetupError("기록이 갱신되어 초기화하지 않았어요. 새로고침 후 범위를 다시 확인해 주세요.") from None
                return f"{name} · 초기화 완료"
            self.task(operation)
        self.button(row, "취소", dialog.destroy, "secondary").pack(side="left")
        self.button(row, "초기화", reset, "danger").pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        dialog.update_idletasks()
        height = frame.winfo_reqheight()
        x = self.window.winfo_rootx() + (self.window.winfo_width() - width) // 2
        y = self.window.winfo_rooty() + (self.window.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.grab_set()
        dialog.focus_set()

    def _help(self):
        data = self.card(self.body, "로컬 데이터")
        data.pack(fill="x")
        self.label(data, str(self.config.data_dir), 9, MUTED, wrap=True).pack(anchor="w", fill="x", pady=(6, 8))
        self.button(data, "폴더 열기", lambda: os.startfile(self.config.data_dir), "secondary").pack(anchor="w")

    def task(self, operation):
        if self.busy:
            return
        self.busy = True
        for button in self.controls:
            if button.winfo_exists():
                button.configure(state="disabled")
        self.message.set("처리 중…")
        def work():
            try:
                result = str(operation())
            except (ClientSetupError, CaptureError) as error:
                result = str(error)
            except Exception:
                result = "처리하지 못했어요. 입력값·연결 상태·폴더 권한을 확인해 주세요."
            def done():
                self.busy = False
                self.message.set(result)
                if self.window.winfo_exists():
                    for button in self.navigation.values():
                        button.configure(state="normal")
                    self.render(self.page)
            self.post(done)
        threading.Thread(target=work, daemon=True, name="MapleMate settings").start()
