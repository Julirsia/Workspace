from pathlib import Path

root = Path("futo")

mapper_path = root / "java/src/org/futo/inputmethod/event/KoreanHardwareKeyMapper.java"
mapper_path.write_text(
    r'''/*
 * Physical QWERTY to Korean Dubeolsik mapping for FUTO Keyboard.
 *
 * FUTO's stock HardwareKeyboardEventDecoder emits KeyEvent#getUnicodeChar(),
 * which is Latin on ordinary hardware keyboards. The KoreanCombiner only
 * accepts Hangul compatibility jamo, so physical input otherwise bypasses
 * Korean composition even while the Korean subtype is active.
 */
package org.futo.inputmethod.event;

import android.view.KeyEvent;

import java.util.Locale;

public final class KoreanHardwareKeyMapper {
    private KoreanHardwareKeyMapper() {}

    private static final int DISALLOWED_META_STATES =
            KeyEvent.META_CTRL_ON
                    | KeyEvent.META_ALT_ON
                    | KeyEvent.META_META_ON
                    | KeyEvent.META_SYM_ON
                    | KeyEvent.META_FUNCTION_ON;

    /**
     * Replaces a printable Latin hardware event with its Dubeolsik jamo event
     * while Korean is the active FUTO subtype. Shortcut chords are left alone.
     */
    public static Event remapIfNeeded(
            final Event original,
            final KeyEvent keyEvent,
            final Locale activeLocale) {
        if (original == null || keyEvent == null || activeLocale == null) {
            return original;
        }
        if (!"ko".equals(activeLocale.getLanguage())) {
            return original;
        }
        if (!original.isHandled()
                || original.getEventType() != Event.EVENT_TYPE_INPUT_KEYPRESS) {
            return original;
        }

        final int normalizedMeta = KeyEvent.normalizeMetaState(keyEvent.getMetaState());
        if ((normalizedMeta & DISALLOWED_META_STATES) != 0) {
            // Preserve Ctrl+A/C/V, Alt/Meta shortcuts, etc.
            return original;
        }

        final int mappedCodePoint = mapKeyCode(
                keyEvent.getKeyCode(),
                keyEvent.isShiftPressed());
        if (mappedCodePoint == Event.NOT_A_CODE_POINT) {
            return original;
        }

        return Event.createHardwareKeypressEvent(
                mappedCodePoint,
                keyEvent.getKeyCode(),
                original.mNextEvent,
                original.isKeyRepeat());
    }

    /** Maps Android physical key codes to Korean compatibility jamo. */
    public static int mapKeyCode(final int keyCode, final boolean shifted) {
        switch (keyCode) {
            // QWERTY top row: ㅂㅈㄷㄱㅅ ㅛㅕㅑㅐㅔ
            case KeyEvent.KEYCODE_Q: return shifted ? 'ㅃ' : 'ㅂ';
            case KeyEvent.KEYCODE_W: return shifted ? 'ㅉ' : 'ㅈ';
            case KeyEvent.KEYCODE_E: return shifted ? 'ㄸ' : 'ㄷ';
            case KeyEvent.KEYCODE_R: return shifted ? 'ㄲ' : 'ㄱ';
            case KeyEvent.KEYCODE_T: return shifted ? 'ㅆ' : 'ㅅ';
            case KeyEvent.KEYCODE_Y: return 'ㅛ';
            case KeyEvent.KEYCODE_U: return 'ㅕ';
            case KeyEvent.KEYCODE_I: return 'ㅑ';
            case KeyEvent.KEYCODE_O: return shifted ? 'ㅒ' : 'ㅐ';
            case KeyEvent.KEYCODE_P: return shifted ? 'ㅖ' : 'ㅔ';

            // Home row: ㅁㄴㅇㄹㅎ ㅗㅓㅏㅣ
            case KeyEvent.KEYCODE_A: return 'ㅁ';
            case KeyEvent.KEYCODE_S: return 'ㄴ';
            case KeyEvent.KEYCODE_D: return 'ㅇ';
            case KeyEvent.KEYCODE_F: return 'ㄹ';
            case KeyEvent.KEYCODE_G: return 'ㅎ';
            case KeyEvent.KEYCODE_H: return 'ㅗ';
            case KeyEvent.KEYCODE_J: return 'ㅓ';
            case KeyEvent.KEYCODE_K: return 'ㅏ';
            case KeyEvent.KEYCODE_L: return 'ㅣ';

            // Bottom row: ㅋㅌㅊㅍㅠ ㅜㅡ
            case KeyEvent.KEYCODE_Z: return 'ㅋ';
            case KeyEvent.KEYCODE_X: return 'ㅌ';
            case KeyEvent.KEYCODE_C: return 'ㅊ';
            case KeyEvent.KEYCODE_V: return 'ㅍ';
            case KeyEvent.KEYCODE_B: return 'ㅠ';
            case KeyEvent.KEYCODE_N: return 'ㅜ';
            case KeyEvent.KEYCODE_M: return 'ㅡ';

            default: return Event.NOT_A_CODE_POINT;
        }
    }
}
''',
    encoding="utf-8",
)

legacy_path = root / "java/src/org/futo/inputmethod/latin/LatinIMELegacy.java"
legacy = legacy_path.read_text(encoding="utf-8")

import_anchor = "import org.futo.inputmethod.event.HardwareKeyboardEventDecoder;\n"
mapper_import = "import org.futo.inputmethod.event.KoreanHardwareKeyMapper;\n"
if mapper_import not in legacy:
    if import_anchor not in legacy:
        raise SystemExit("HardwareKeyboardEventDecoder import anchor not found")
    legacy = legacy.replace(import_anchor, import_anchor + mapper_import, 1)

old_decode = '''        final Event event = getHardwareKeyEventDecoder(
                keyEvent.getDeviceId()).decodeHardwareKey(keyEvent);
'''
new_decode = '''        Event event = getHardwareKeyEventDecoder(
                keyEvent.getDeviceId()).decodeHardwareKey(keyEvent);
        event = KoreanHardwareKeyMapper.remapIfNeeded(event, keyEvent, mLocale);
'''
if new_decode not in legacy:
    if old_decode not in legacy:
        raise SystemExit("LatinIMELegacy hardware decode anchor not found")
    legacy = legacy.replace(old_decode, new_decode, 1)

legacy_path.write_text(legacy, encoding="utf-8")

# Build-harness validation: all 26 alphabetic key positions must be mapped,
# and the remap must run before the event is dispatched to the active IME.
mapper = mapper_path.read_text(encoding="utf-8")
patched_legacy = legacy_path.read_text(encoding="utf-8")

expected_keys = "QWERTYUIOPASDFGHJKLZXCVBNM"
for key in expected_keys:
    assert f"KeyEvent.KEYCODE_{key}" in mapper, key
assert mapper.count("case KeyEvent.KEYCODE_") == 26

for required in (
    "case KeyEvent.KEYCODE_R: return shifted ? 'ㄲ' : 'ㄱ';",
    "case KeyEvent.KEYCODE_K: return 'ㅏ';",
    "case KeyEvent.KEYCODE_S: return 'ㄴ';",
    "case KeyEvent.KEYCODE_G: return 'ㅎ';",
    "case KeyEvent.KEYCODE_M: return 'ㅡ';",
    "case KeyEvent.KEYCODE_F: return 'ㄹ';",
    "KeyEvent.META_CTRL_ON",
    "KeyEvent.META_ALT_ON",
    "original.isKeyRepeat()",
):
    assert required in mapper, required

call = "event = KoreanHardwareKeyMapper.remapIfNeeded(event, keyEvent, mLocale);"
dispatch = ").onEvent(event);"
assert patched_legacy.count(mapper_import) == 1
assert patched_legacy.count(call) == 1
assert patched_legacy.index(call) < patched_legacy.index(dispatch, patched_legacy.index(call))

print("Patched and verified Korean Dubeolsik physical-key mapping")
print("gksrmf key positions map to ㅎㅏㄴㄱㅡㄹ, which KoreanCombiner composes as 한글")
