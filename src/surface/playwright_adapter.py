from __future__ import annotations

from typing import Dict, List, Optional

from playwright.sync_api import Locator, Page, sync_playwright

from src.surface.locator_strategy import ElementDescriptor, build_locator_bundle
from src.surface.surface_adapter import PerceivedElement, Snapshot, SurfaceAdapter
from src.types.action import LocatorBundle, LocatorCandidate, WaitCondition

# In-browser extraction of interactive elements as plain, JSON-serializable
# descriptors. Deliberately DOM-first rather than relying on Playwright's
# accessibility-tree API: this gives us exactly the fields locator_strategy.py
# needs to rank locators, and it works identically on the intentionally
# legacy, non-semantic markup this system targets, where a real accessibility
# tree is often sparse anyway.
_EXTRACT_SCRIPT = """
(() => {
  function cssPath(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let selector = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (siblings.length > 1) {
          selector += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
        }
      }
      parts.unshift(selector);
      node = parent;
    }
    return parts.join(' > ');
  }
  function xpath(el) {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1) {
      let index = 1;
      let sib = node.previousElementSibling;
      while (sib) {
        if (sib.tagName === node.tagName) index++;
        sib = sib.previousElementSibling;
      }
      parts.unshift(node.tagName.toLowerCase() + '[' + index + ']');
      node = node.parentElement;
    }
    return '/' + parts.join('/');
  }
  function isVisible(el) {
    const r = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }
  function implicitRole(el) {
    const tag = el.tagName.toLowerCase();
    if (el.getAttribute('role')) return el.getAttribute('role');
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'submit' || type === 'button') return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      return 'textbox';
    }
    // Not a real interactive control -- but it was matched (has an id, e.g. a legacy app's
    // "this cell is meaningful" marker) and isn't hidden, so it's read-only display data the
    // agent needs to be able to see and extract (a balance, a status), not click or type into.
    return 'text';
  }
  function accessibleName(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    if (el.id) {
      const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lbl && lbl.textContent) return lbl.textContent.trim();
    }
    const parentLabel = el.closest('label');
    if (parentLabel && parentLabel.textContent) return parentLabel.textContent.trim();
    if ('value' in el && (el.type === 'submit' || el.type === 'button') && el.value) return el.value.trim();
    const text = (el.innerText || el.textContent || '').trim();
    if (text) return text.slice(0, 80);
    const placeholder = el.getAttribute('placeholder');
    if (placeholder) return placeholder.trim();
    return '';
  }

  // [id] is included specifically so read-only display data the agent needs to read (a
  // balance, a status) is perceivable at all -- without it, only clickable/typeable controls
  // are visible, and a goal that's purely "read this value" has nothing to target.
  const nodes = Array.from(document.querySelectorAll('a[href], button, input, select, textarea, [role], [onclick], [id]'));
  const elements = nodes
    .filter((el) => isVisible(el))
    .slice(0, 200)
    .map((el) => {
      const role = implicitRole(el);
      const isFormControl = 'value' in el;
      return {
        tagName: el.tagName.toLowerCase(),
        role,
        accessibleName: accessibleName(el),
        idAttr: el.id || undefined,
        nameAttr: el.getAttribute('name') || undefined,
        testIdAttr: el.getAttribute('data-testid') || undefined,
        text: (el.innerText || el.textContent || '').trim().slice(0, 80),
        // Form controls show their actual .value; a plain "text" element (matched only via
        // [id], not a real control) shows its text content instead, so its displayed data is
        // visible in the observation without needing a separate extract call to see what's there.
        value: isFormControl ? String(el.value ?? '') : (role === 'text' ? (el.innerText || el.textContent || '').trim().slice(0, 80) : undefined),
        cssPath: cssPath(el),
        xpath: xpath(el),
      };
    });

  const bannerNodes = Array.from(
    document.querySelectorAll('.error, .banner, .alert, .notice, .success, .message, [role="alert"]')
  )
    .filter((el) => isVisible(el))
    .map((el) => (el.innerText || el.textContent || '').trim())
    .filter((t) => t.length > 0);

  return { elements, bannerText: bannerNodes.join(' | '), title: document.title, url: location.href };
})()
"""


class PlaywrightAdapter(SurfaceAdapter):
    def __init__(self, headed: bool = True, cdp_port: int = 9222):
        self._cdp_port = cdp_port
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=not headed, args=[f"--remote-debugging-port={cdp_port}"]
        )
        self._context = self._browser.new_context()
        self._page: Page = self._context.new_page()
        self._last_ref_map: Dict[str, ElementDescriptor] = {}

    def perceive(self) -> Snapshot:
        raw = self._page.evaluate(_EXTRACT_SCRIPT)
        self._last_ref_map.clear()
        elements: List[PerceivedElement] = []
        for i, desc in enumerate(raw["elements"]):
            ref = f"e{i + 1}"
            self._last_ref_map[ref] = ElementDescriptor(
                tag_name=desc["tagName"],
                role=desc.get("role"),
                accessible_name=desc.get("accessibleName"),
                id_attr=desc.get("idAttr"),
                name_attr=desc.get("nameAttr"),
                test_id_attr=desc.get("testIdAttr"),
                text=desc.get("text"),
                css_path=desc["cssPath"],
                xpath=desc["xpath"],
            )
            elements.append(
                PerceivedElement(
                    ref=ref,
                    role=desc.get("role") or "generic",
                    name=desc.get("accessibleName") or "",
                    value=desc.get("value"),
                )
            )
        return Snapshot(url=raw["url"], title=raw["title"], elements=elements, banner_text=raw.get("bannerText") or None)

    def resolve_element_ref(self, ref: str) -> LocatorBundle:
        desc = self._last_ref_map.get(ref)
        if desc is None:
            raise ValueError(f"Unknown element ref '{ref}'. Call perceive() again before acting.")
        return build_locator_bundle(desc)

    def navigate(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded")

    def click(self, locator: LocatorBundle) -> None:
        loc = self._resolve_to_playwright(locator)
        loc.click(timeout=10_000)

    def type_text(self, locator: LocatorBundle, value: str) -> None:
        loc = self._resolve_to_playwright(locator)
        loc.fill(value, timeout=10_000)

    def select_option(self, locator: LocatorBundle, value: str) -> None:
        loc = self._resolve_to_playwright(locator)
        loc.select_option(value, timeout=10_000)

    def extract_text(self, locator: LocatorBundle) -> str:
        loc = self._resolve_to_playwright(locator)
        return loc.inner_text(timeout=10_000).strip()

    def is_visible(self, locator: LocatorBundle) -> bool:
        try:
            loc = self._resolve_to_playwright(locator, silent=True)
            return loc.is_visible()
        except Exception:
            return False

    def wait_for(self, condition: WaitCondition) -> bool:
        try:
            if condition.kind == "network_idle":
                self._page.wait_for_load_state("networkidle", timeout=condition.timeout_ms)
                return True
            if condition.kind == "url_matches":
                import re as _re

                self._page.wait_for_url(_re.compile(condition.pattern), timeout=condition.timeout_ms)
                return True
            if condition.kind == "element_visible":
                loc = self._resolve_to_playwright(condition.locator, silent=True)
                loc.wait_for(state="visible", timeout=condition.timeout_ms)
                return True
            if condition.kind == "element_hidden":
                loc = self._resolve_to_playwright(condition.locator, silent=True)
                loc.wait_for(state="hidden", timeout=condition.timeout_ms)
                return True
        except Exception:
            return False
        return False

    def current_url(self) -> str:
        return self._page.url

    def screenshot(self, dest_path: str) -> None:
        self._page.screenshot(path=dest_path, full_page=True)

    def get_handoff_url(self) -> Optional[str]:
        return f"http://localhost:{self._cdp_port}"

    def close(self) -> None:
        self._browser.close()
        self._playwright.stop()

    def _resolve_to_playwright(self, bundle: LocatorBundle, silent: bool = False) -> Locator:
        """The core of "stable element/control targeting" at replay time: try the primary
        locator, and on failure to resolve to exactly one element, fall through the ranked
        fallbacks in order. Only after every candidate fails does this raise -- which the
        Replay Executor treats as a hard failure."""
        candidates = [bundle.primary, *bundle.fallbacks]
        errors: List[str] = []
        for candidate in candidates:
            try:
                loc = self._to_playwright_locator(candidate)
                if loc.count() >= 1:
                    return loc.first
                errors.append(f"{candidate.strategy}:{candidate.value} -> 0 matches")
            except Exception as err:  # noqa: BLE001 - deliberately broad: any candidate may legitimately fail
                errors.append(f"{candidate.strategy}:{candidate.value} -> {err}")
        if not silent:
            raise RuntimeError(f"No locator candidate resolved. Tried: {'; '.join(errors)}")
        raise RuntimeError("silent-miss")

    def _to_playwright_locator(self, candidate: LocatorCandidate) -> Locator:
        if candidate.strategy == "role":
            return self._page.get_by_role(candidate.value, name=candidate.role_name)  # type: ignore[arg-type]
        if candidate.strategy == "label":
            return self._page.get_by_label(candidate.value)
        if candidate.strategy == "text":
            return self._page.get_by_text(candidate.value, exact=False)
        if candidate.strategy == "css":
            return self._page.locator(candidate.value)
        if candidate.strategy == "xpath":
            return self._page.locator(f"xpath={candidate.value}")
        raise ValueError(f"Unknown locator strategy '{candidate.strategy}'.")
