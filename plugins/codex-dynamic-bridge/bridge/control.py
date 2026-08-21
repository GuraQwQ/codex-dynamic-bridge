from urllib.parse import urlparse


MUTATING_ACTIONS = {
    "click",
    "fill",
    "press",
    "click-role",
    "fill-role",
    "select-role",
    "shortcut",
}


class ControlError(RuntimeError):
    """可直接展示给用户的页面控制错误。"""


def conversation_id_from_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0] != "c":
        return None
    return path_parts[1]


def page_info(page):
    return page.evaluate(
        """() => {
            const active = document.activeElement;
            return {
                title: document.title,
                url: location.href,
                hasFocus: document.hasFocus(),
                readyState: document.readyState,
                viewport: {width: innerWidth, height: innerHeight},
                activeElement: active ? {
                    tag: active.tagName.toLowerCase(),
                    id: active.id || null,
                    role: active.getAttribute('role'),
                    ariaLabel: active.getAttribute('aria-label')
                } : null,
                counts: {
                    buttons: document.querySelectorAll('button').length,
                    inputs: document.querySelectorAll('input').length,
                    textareas: document.querySelectorAll('textarea').length,
                    contentEditables: document.querySelectorAll('[contenteditable="true"]').length
                }
            };
        }"""
    )


def describe_target(target):
    details = target.evaluate(
        """element => {
            const rawText = element.innerText ?? element.textContent ?? '';
            const value = 'value' in element ? String(element.value ?? '') : rawText;
            return {
                tag: element.tagName.toLowerCase(),
                id: element.id || null,
                role: element.getAttribute('role'),
                ariaLabel: element.getAttribute('aria-label'),
                inputType: element.getAttribute('type'),
                text: String(rawText).trim().slice(0, 200),
                valueLength: value.length
            };
        }"""
    )
    try:
        editable = target.is_editable()
    except Exception:
        editable = False
    details.update(
        {
            "visible": target.is_visible(),
            "enabled": target.is_enabled(),
            "editable": editable,
        }
    )
    return details


def describe_target_after_action(target):
    try:
        return describe_target(target)
    except Exception as exc:
        reason = str(exc).splitlines()[0][:200]
        return {"available": False, "reason": reason}


def read_target_text(target, timeout_ms):
    """读取目标元素的完整可见文本，不执行页面修改。"""
    text = target.inner_text(timeout=timeout_ms).strip()
    return {"text": text, "textLength": len(text)}


def snapshot_target(target, timeout_ms, max_controls):
    """返回可访问性快照和可交互控件摘要。"""
    snapshot = target.aria_snapshot(timeout=timeout_ms)
    controls = target.locator(
        "button, a, input, textarea, select, [role], [contenteditable='true'], [tabindex]"
    ).evaluate_all(
        """(elements, limit) => elements.slice(0, limit).map((element, index) => {
            const text = String(element.innerText ?? element.textContent ?? '').trim();
            const role = element.getAttribute('role') || ({
                BUTTON: 'button', A: 'link', INPUT: 'textbox',
                TEXTAREA: 'textbox', SELECT: 'combobox'
            }[element.tagName] || null);
            const name = element.getAttribute('aria-label')
                || element.getAttribute('title')
                || element.getAttribute('placeholder')
                || text.slice(0, 160);
            const rect = element.getBoundingClientRect();
            return {
                index,
                role,
                name: name || null,
                tag: element.tagName.toLowerCase(),
                type: element.getAttribute('type'),
                visible: rect.width > 0 && rect.height > 0,
                disabled: Boolean(element.disabled) || element.getAttribute('aria-disabled') === 'true'
            };
        })""",
        max_controls,
    )
    return {"aria": snapshot, "controls": controls, "controlCount": len(controls)}


def resolve_target(page, selector, nth=None):
    locator = page.locator(selector)
    count = locator.count()
    if nth is None:
        if count != 1:
            raise ControlError(f"选择器必须唯一匹配，实际匹配 {count} 个元素: {selector}")
        index = 0
    else:
        if nth < 0:
            raise ControlError("--nth 不能小于 0")
        if nth >= count:
            raise ControlError(f"--nth {nth} 超出匹配范围，选择器仅匹配 {count} 个元素")
        index = nth
    return locator.nth(index), count, index


def resolve_role_target(page, role, name, exact=True, nth=None):
    locator = page.get_by_role(role, name=name, exact=exact)
    count = locator.count()
    description = f"role={role}, name={name!r}, exact={exact}"
    if nth is None:
        if count != 1:
            raise ControlError(f"语义目标必须唯一匹配，实际匹配 {count} 个元素: {description}")
        index = 0
    else:
        if nth < 0:
            raise ControlError("--nth 不能小于 0")
        if nth >= count:
            raise ControlError(f"--nth {nth} 超出匹配范围，语义目标仅匹配 {count} 个元素")
        index = nth
    return locator.nth(index), count, index


def wait_for_target(page, selector, state, timeout_ms, nth=None):
    locator = page.locator(selector)
    index = nth if nth is not None else 0
    if index < 0:
        raise ControlError("--nth 不能小于 0")
    locator.nth(index).wait_for(state=state, timeout=timeout_ms)
    count = locator.count()
    if nth is not None and state in {"attached", "visible"} and nth >= count:
        raise ControlError(f"--nth {nth} 超出匹配范围，选择器仅匹配 {count} 个元素")
    if nth is None and state in {"attached", "visible"} and count != 1:
        raise ControlError(f"等待后选择器并非唯一匹配，实际匹配 {count} 个元素: {selector}")
    return {
        "action": "wait",
        "selector": selector,
        "nth": nth,
        "state": state,
        "matchCount": count,
    }


def unique_text_target(page, text, exact=True, nth=None):
    locator = page.get_by_text(text, exact=exact)
    count = locator.count()
    if nth is None:
        if count != 1:
            raise ControlError(f"文本目标必须唯一匹配，实际匹配 {count} 个元素: {text}")
        index = 0
    else:
        if nth < 0 or nth >= count:
            raise ControlError(f"文本目标序号 {nth} 超出匹配范围，实际匹配 {count} 个元素")
        index = nth
    return locator.nth(index)


def message_input(page):
    candidates = (
        ("combobox", "Message input"),
        ("textbox", "Message input"),
        ("combobox", "消息输入"),
        ("textbox", "消息输入"),
    )
    for role, name in candidates:
        locator = page.get_by_role(role, name=name, exact=True)
        if locator.count() == 1:
            return locator.nth(0)
    locator = page.locator('[contenteditable="true"]')
    if locator.count() == 1:
        return locator.nth(0)
    raise ControlError("无法唯一定位消息输入框")


def submit_slash_command(page, command, timeout_ms):
    target = message_input(page)
    target.fill(command, timeout=timeout_ms)
    target.press("Enter", timeout=timeout_ms)


def click_first_named(page, names, timeout_ms):
    for name in names:
        locator = page.get_by_text(name, exact=True)
        if locator.count() == 1:
            locator.nth(0).click(timeout=timeout_ms)
            return name
    raise ControlError(f"未找到唯一入口: {' / '.join(names)}")


def open_model_menu(page, timeout_ms, trigger_name=None):
    if trigger_name:
        target, _, _ = resolve_role_target(
            page, "button", trigger_name, exact=False, nth=None
        )
        target.click(timeout=timeout_ms)
        return trigger_name
    buttons = page.locator("button")
    matches = []
    for index in range(buttons.count()):
        button = buttons.nth(index)
        try:
            text = button.inner_text(timeout=timeout_ms).strip()
        except Exception:
            continue
        if text.startswith(("Gemini ", "Claude ", "GPT-OSS", "GPT ")):
            matches.append((button, text))
    if len(matches) != 1:
        raise ControlError(f"无法唯一识别模型选择器，实际候选 {len(matches)} 个")
    matches[0][0].click(timeout=timeout_ms)
    return matches[0][1]


def perform_workflow(page, args):
    action = args.workflow_action
    before = page_info(page)
    detail = {}
    if action == "conversation-new":
        page.keyboard.press("Control+N")
    elif action == "conversation-switch":
        page.keyboard.press("Control+K")
        page.wait_for_timeout(args.settle_ms)
        page.keyboard.type(args.target)
        page.keyboard.press("Enter")
        detail["target"] = args.target
    elif action == "conversation-rename":
        submit_slash_command(page, f"/rename {args.name}", args.timeout_ms)
        detail["name"] = args.name
    elif action == "conversation-fork":
        command = "/fork" if not args.project_id else f"/fork {args.project_id}"
        submit_slash_command(page, command, args.timeout_ms)
        detail["projectId"] = args.project_id
    elif action == "conversation-cancel":
        page.keyboard.press("Escape")
    elif action == "model-list" or action == "usage":
        detail["currentModel"] = open_model_menu(
            page, args.timeout_ms, getattr(args, "trigger_name", None)
        )
        page.wait_for_timeout(args.settle_ms)
        detail["snapshot"] = snapshot_target(
            page.locator("body"), args.timeout_ms, args.max_controls
        )
        page.keyboard.press("Escape")
    elif action == "model-set":
        current = open_model_menu(page, args.timeout_ms, args.trigger_name)
        page.wait_for_timeout(args.settle_ms)
        if args.model not in current:
            unique_text_target(
                page,
                args.model,
                exact=not args.contains,
                nth=args.nth,
            ).click(timeout=args.timeout_ms)
            detail["changed"] = True
        else:
            page.keyboard.press("Escape")
            detail["changed"] = False
        detail.update({"before": current, "model": args.model})
    elif action == "project-open":
        click_first_named(page, ("项目列表", "Projects"), args.timeout_ms)
        page.wait_for_timeout(args.settle_ms)
        unique_text_target(page, args.name, exact=True).click(timeout=args.timeout_ms)
        detail["project"] = args.name
    elif action == "project-new":
        click_first_named(page, ("项目列表", "Projects"), args.timeout_ms)
        page.wait_for_timeout(args.settle_ms)
        detail["entry"] = click_first_named(
            page, ("新建项目", "New Project"), args.timeout_ms
        )
    elif action in {"settings-open", "settings-read", "settings-set"}:
        page.keyboard.press("Control+Comma")
        page.wait_for_timeout(args.settle_ms)
        if action == "settings-read":
            detail["snapshot"] = snapshot_target(
                page.locator("body"), args.timeout_ms, args.max_controls
            )
        elif action == "settings-set":
            target = page.get_by_label(args.label, exact=True)
            if target.count() != 1:
                raise ControlError(f"设置标签必须唯一匹配: {args.label}")
            target = target.nth(0)
            kind = target.evaluate(
                "element => ({tag: element.tagName.toLowerCase(), type: element.type || null})"
            )
            if kind["type"] in {"checkbox", "radio"}:
                enabled = args.value.lower() in {"1", "true", "on", "yes"}
                if enabled:
                    target.check(timeout=args.timeout_ms)
                else:
                    target.uncheck(timeout=args.timeout_ms)
            elif kind["tag"] == "select":
                target.select_option(args.value, timeout=args.timeout_ms)
            else:
                target.fill(args.value, timeout=args.timeout_ms)
            detail.update({"label": args.label, "value": args.value})
    elif action == "schedule-open":
        detail["entry"] = click_first_named(
            page, ("计划任务", "Scheduled Tasks"), args.timeout_ms
        )
    elif action == "artifact-proceed":
        detail["button"] = click_first_named(
            page, ("Proceed", "继续", "执行"), args.timeout_ms
        )
    else:
        raise ControlError(f"不支持的桌面工作流: {action}")

    page.wait_for_timeout(args.settle_ms)
    return {
        "action": "workflow",
        "workflow": action,
        "detail": detail,
        "pageBefore": before,
        "pageAfter": page_info(page),
        "completed": True,
    }


def perform_action(page, args):
    action = args.control_action
    if action == "inspect":
        return {"action": action, "page": page_info(page)}
    if action == "workflow":
        return perform_workflow(page, args)
    if action == "shortcut":
        before = page_info(page)
        page.keyboard.press(args.key)
        page.wait_for_timeout(args.settle_ms)
        return {
            "action": action,
            "key": args.key,
            "pageBefore": before,
            "pageAfter": page_info(page),
            "completed": True,
        }
    if action == "wait":
        result = wait_for_target(
            page,
            args.selector,
            args.state,
            args.timeout_ms,
            args.nth,
        )
        result["page"] = page_info(page)
        return result

    if action == "snapshot":
        target, count, index = resolve_target(page, args.selector, args.nth)
        return {
            "action": action,
            "selector": args.selector,
            "matchCount": count,
            "nth": index,
            "snapshot": snapshot_target(target, args.timeout_ms, args.max_controls),
            "page": page_info(page),
        }

    if action in {"click-role", "fill-role", "select-role"}:
        target, count, index = resolve_role_target(
            page,
            args.role,
            args.name,
            exact=not args.contains,
            nth=args.nth,
        )
        before = describe_target(target)
        if action == "click-role":
            target.click(timeout=args.timeout_ms)
        elif action == "fill-role":
            target.fill(args.text, timeout=args.timeout_ms)
        else:
            target.select_option(args.value, timeout=args.timeout_ms)
        page.wait_for_timeout(args.settle_ms)
        return {
            "action": action,
            "role": args.role,
            "name": args.name,
            "matchCount": count,
            "nth": index,
            "targetBefore": before,
            "targetAfter": describe_target_after_action(target),
            "page": page_info(page),
            "completed": True,
        }

    target, count, index = resolve_target(page, args.selector, args.nth)
    if action == "read":
        return {
            "action": action,
            "selector": args.selector,
            "matchCount": count,
            "nth": index,
            "content": read_target_text(target, args.timeout_ms),
            "page": page_info(page),
        }

    before = describe_target(target)
    if action == "get":
        return {
            "action": action,
            "selector": args.selector,
            "matchCount": count,
            "nth": index,
            "target": before,
            "page": page_info(page),
        }
    if action == "click":
        target.click(timeout=args.timeout_ms)
    elif action == "fill":
        target.fill(args.text, timeout=args.timeout_ms)
    elif action == "press":
        target.press(args.key, timeout=args.timeout_ms)
    else:
        raise ControlError(f"不支持的控制动作: {action}")

    page.wait_for_timeout(args.settle_ms)
    return {
        "action": action,
        "selector": args.selector,
        "matchCount": count,
        "nth": index,
        "targetBefore": before,
        "targetAfter": describe_target_after_action(target),
        "page": page_info(page),
        "completed": True,
    }


def execute_control(port, conversation_id, args):
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ControlError(
            "控制模式需要 Python Playwright；插件不会自动安装依赖，请先提供可用运行时"
        ) from exc

    playwright = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        pages = [page for context in browser.contexts for page in context.pages]
        matches = [
            page
            for page in pages
            if conversation_id_from_url(page.url) == conversation_id
        ]
        if not matches:
            raise ControlError(f"CDP 中未找到指定会话页面: {conversation_id}")
        if len(matches) > 1:
            raise ControlError(f"CDP 中出现多个同 ID 会话页面: {conversation_id}")
        page = matches[0]
        page.set_default_timeout(args.timeout_ms)
        return perform_action(page, args)
    except ControlError:
        raise
    except PlaywrightTimeoutError as exc:
        raise ControlError(f"控制动作等待超时: {exc}") from exc
    except PlaywrightError as exc:
        raise ControlError(f"CDP 控制失败: {exc}") from exc
    finally:
        if playwright is not None:
            playwright.stop()
