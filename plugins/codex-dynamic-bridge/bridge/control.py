from urllib.parse import urlparse


MUTATING_ACTIONS = {"click", "fill", "press"}


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


def perform_action(page, args):
    action = args.control_action
    if action == "inspect":
        return {"action": action, "page": page_info(page)}
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
