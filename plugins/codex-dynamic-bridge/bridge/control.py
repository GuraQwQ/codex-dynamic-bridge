from urllib.parse import parse_qs, urlparse


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


def trusted_page_url(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def page_info(page):
    return page.evaluate(
        r"""() => {
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


def approval_dialog_snapshot(page):
    """只读取当前权限对话框；内容不写入事件或任务账本。"""
    return page.evaluate(
        r"""() => {
            const markers = [
                '是否允许运行此命令',
                '允许运行此命令',
                'Allow this command',
                'permission to run this command'
            ];
            const visible = element => {
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return rect.width > 0 && rect.height > 0
                    && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const containsMarker = element => {
                const text = String(element.innerText || element.textContent || '');
                return markers.some(marker => text.includes(marker));
            };
            let target = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')]
                .find(element => visible(element) && containsMarker(element));
            if (!target) {
                const leaves = [...document.querySelectorAll('body *')].filter(element => {
                    if (!visible(element) || !containsMarker(element)) return false;
                    return ![...element.children].some(child => containsMarker(child));
                });
                for (const leaf of leaves) {
                    let current = leaf;
                    for (let depth = 0; current && depth < 8; depth += 1) {
                        if (current.querySelectorAll('button').length >= 1) {
                            target = current;
                            break;
                        }
                        current = current.parentElement;
                    }
                    if (target) break;
                }
            }
            if (!target) return {open: false, text: '', textLength: 0, buttons: []};
            const rawText = String(target.innerText || target.textContent || '').trim();
            const buttons = [...target.querySelectorAll('button')]
                .filter(visible)
                .map(button => String(
                    button.getAttribute('aria-label')
                    || button.getAttribute('title')
                    || button.innerText
                    || button.textContent
                    || ''
                ).trim())
                .filter(Boolean);
            const options = [...target.querySelectorAll('input[type="radio"]')]
                .filter(visible)
                .map(input => {
                    const explicit = input.id
                        ? document.querySelector(`label[for="${CSS.escape(input.id)}"]`)
                        : null;
                    const label = explicit || input.closest('label') || input.parentElement;
                    const name = String(label?.innerText || label?.textContent || '').trim();
                    const denied = /(^|\s)(no|deny|denied)(\s|$)|否|拒绝|不允许/i.test(name);
                    return {name, decision: denied ? 'deny' : 'allow', checked: input.checked};
                })
                .filter(option => option.name);
            return {
                open: true,
                text: rawText.slice(0, 4000),
                textLength: rawText.length,
                buttons,
                options
            };
        }"""
    )


def queued_send_now_target(page, prompt):
    buttons = page.get_by_role("button", name="Send Now", exact=True)
    matches = []
    for index in range(buttons.count()):
        button = buttons.nth(index)
        if button.evaluate(
            """(candidate, expected) => {
                let current = candidate.parentElement;
                for (let depth = 0; current && depth < 8; depth += 1) {
                    const text = String(current.innerText || current.textContent || '');
                    if (text.includes(expected)) return true;
                    if (depth > 0 && /Queued Messages|排队消息/.test(text)) return false;
                    current = current.parentElement;
                }
                return false;
            }""",
            prompt,
        ):
            matches.append(button)
    if len(matches) != 1:
        raise ControlError(
            f"无法把补充内容关联到唯一 Send Now 按钮，实际候选 {len(matches)} 个"
        )
    return matches[0]


def review_tab_target(page, timeout_ms):
    review = page.get_by_role("button", name="Review tab", exact=True)
    if review.count() == 0:
        toggles = []
        for name in ("切换辅助面板", "Toggle Auxiliary Panel"):
            candidate = page.get_by_role("button", name=name, exact=True)
            if candidate.count() == 1:
                toggles.append(candidate.nth(0))
        if len(toggles) != 1:
            raise ControlError("Review 页不可见，且无法唯一定位辅助面板开关")
        toggle = toggles[0]
        if toggle.get_attribute("aria-expanded") != "true":
            toggle.click(timeout=timeout_ms)
        review.nth(0).wait_for(state="visible", timeout=timeout_ms)
    target, _, _ = resolve_role_target(
        page, "button", "Review tab", exact=True, nth=None
    )
    return target


def review_region_target(page):
    matches = []
    for name in ("评审", "Review"):
        region = page.get_by_role("region", name=name, exact=True)
        for index in range(region.count()):
            target = region.nth(index)
            if target.is_visible():
                matches.append(target)
    if len(matches) != 1:
        raise ControlError(f"Review 内容区域必须唯一可见，实际候选 {len(matches)} 个")
    return matches[0]


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
        previous_id = conversation_id_from_url(page.url)
        new_button, _, _ = resolve_role_target(
            page,
            "button",
            "New Conversation",
            exact=True,
        )
        new_button.click(timeout=args.timeout_ms)
        page.wait_for_url(
            lambda url: trusted_page_url(url) and conversation_id_from_url(url) is None,
            timeout=args.timeout_ms,
        )
        input_target, _, _ = resolve_role_target(
            page,
            "combobox",
            "Message input",
            exact=True,
        )
        input_target.fill(args.prompt, timeout=args.timeout_ms)
        send_button, _, _ = resolve_role_target(
            page,
            "button",
            "Send message",
            exact=True,
        )
        send_button.click(timeout=args.timeout_ms)
        page.wait_for_url(
            lambda url: (
                conversation_id_from_url(url) is not None
                and conversation_id_from_url(url) != previous_id
            ),
            timeout=args.timeout_ms,
        )
        detail["conversationId"] = conversation_id_from_url(page.url)
        detail["promptLength"] = len(args.prompt)
    elif action == "conversation-send-now":
        input_target = message_input(page)
        input_target.fill(args.prompt, timeout=args.timeout_ms)
        input_target.press("Enter", timeout=args.timeout_ms)
        page.wait_for_timeout(args.settle_ms)
        send_now = queued_send_now_target(page, args.prompt)
        send_now.click(timeout=args.timeout_ms)
        page.wait_for_timeout(args.settle_ms)
        try:
            queued_send_now_target(page, args.prompt)
        except ControlError:
            pass
        else:
            raise ControlError("Send Now 已点击，但补充内容仍在队列中；不要自动重试")
        detail.update({"promptLength": len(args.prompt), "sentImmediately": True})
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
        body, _, _ = resolve_target(page, "body", nth=None)
        detail["snapshot"] = snapshot_target(
            body, args.timeout_ms, args.max_controls
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
    elif action == "approval-inspect":
        detail["approval"] = approval_dialog_snapshot(page)
    elif action == "review-changes":
        query = parse_qs(urlparse(page.url).query)
        review_tab = review_tab_target(page, args.timeout_ms)
        if query.get("tab", [None])[0] != "review":
            review_tab.click(timeout=args.timeout_ms)
            page.wait_for_timeout(args.settle_ms)
        review_region = review_region_target(page)
        detail["content"] = read_target_text(review_region, args.timeout_ms)
        detail["snapshot"] = snapshot_target(
            review_region, args.timeout_ms, args.max_controls
        )
    elif action == "approval-respond":
        before_approval = approval_dialog_snapshot(page)
        if not before_approval.get("open"):
            raise ControlError("当前会话没有可见的命令审批对话框")
        matching_options = [
            option
            for option in before_approval.get("options", [])
            if option.get("name") == args.option_name
            and option.get("decision") == args.decision
        ]
        if len(matching_options) != 1:
            raise ControlError("审批选项与指定 decision 不唯一匹配")
        option = page.get_by_text(args.option_name, exact=True)
        if option.count() != 1:
            raise ControlError("审批选项文本必须唯一匹配")
        option.nth(0).click(timeout=args.timeout_ms)
        if args.button_name not in before_approval.get("buttons", []):
            raise ControlError(
                f"审批按钮不在当前对话框中: {args.button_name}; "
                f"可见按钮: {before_approval.get('buttons', [])}"
            )
        target, _, _ = resolve_role_target(
            page, "button", args.button_name, exact=True, nth=None
        )
        target.click(timeout=args.timeout_ms)
        page.wait_for_timeout(args.settle_ms)
        after_approval = approval_dialog_snapshot(page)
        detail.update(
            {
                "decision": args.decision,
                "option": args.option_name,
                "button": args.button_name,
                "before": before_approval,
                "after": after_approval,
            }
        )
        if after_approval.get("open"):
            raise ControlError("审批按钮已点击，但对话框仍可见；不要自动重试")
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


def execute_control(port, conversation_id, args, page_url=None):
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
        if page_url is not None:
            if not trusted_page_url(page_url):
                raise ControlError(f"拒绝非 Antigravity 回环页面目标: {page_url}")
            matches = [page for page in pages if page.url == page_url]
            target_description = page_url
        else:
            matches = [
                page
                for page in pages
                if conversation_id_from_url(page.url) == conversation_id
            ]
            target_description = conversation_id
        if not matches:
            raise ControlError(f"CDP 中未找到指定 Antigravity 页面: {target_description}")
        if len(matches) > 1:
            raise ControlError(f"CDP 中出现多个相同页面目标: {target_description}")
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
