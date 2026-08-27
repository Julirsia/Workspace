#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_latinime.py <futo-source-root>")

    root = pathlib.Path(sys.argv[1]).resolve()
    path = root / "java/src/org/futo/inputmethod/latin/LatinIME.kt"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "import android.view.inputmethod.InputMethodSubtype\n",
        "import android.view.inputmethod.InputMethodSubtype\nimport android.widget.Toast\n",
        "Toast import",
    )

    text = replace_once(
        text,
        "    val uixManager = UixManager(this)\n",
        "    val uixManager = UixManager(this)\n\n"
        "    private val hardwareKoreanInput = HardwareKoreanInputController()\n",
        "controller field",
    )

    text = replace_once(
        text,
        "        latinIMELegacy.onCreate()\n",
        "        latinIMELegacy.onCreate()\n"
        "        hardwareKoreanInput.setKoreanMode(latinIMELegacy.locale.language == \"ko\")\n",
        "initial mode",
    )

    text = replace_once(
        text,
        "        uixManager.updateLocale(Subtypes.getLocale(subtype))\n",
        "        uixManager.updateLocale(Subtypes.getLocale(subtype))\n"
        "        hardwareKoreanInput.setKoreanMode(Subtypes.getLocale(subtype).language == \"ko\")\n",
        "subtype mode sync",
    )

    text = replace_once(
        text,
        "    override fun onStartInput(attribute: EditorInfo?, restarting: Boolean) {\n"
        "        super.onStartInput(attribute, restarting)\n",
        "    override fun onStartInput(attribute: EditorInfo?, restarting: Boolean) {\n"
        "        hardwareKoreanInput.resetTracking()\n"
        "        super.onStartInput(attribute, restarting)\n",
        "start input reset",
    )

    text = replace_once(
        text,
        "    override fun onFinishInputView(finishingInput: Boolean) {\n"
        "        super.onFinishInputView(finishingInput)\n",
        "    override fun onFinishInputView(finishingInput: Boolean) {\n"
        "        hardwareKoreanInput.resetTracking()\n"
        "        super.onFinishInputView(finishingInput)\n",
        "finish input view reset",
    )

    text = replace_once(
        text,
        "    override fun onFinishInput() {\n"
        "        super.onFinishInput()\n",
        "    override fun onFinishInput() {\n"
        "        hardwareKoreanInput.resetTracking()\n"
        "        super.onFinishInput()\n",
        "finish input reset",
    )

    old_keys = (
        "    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {\n"
        "        return latinIMELegacy.onKeyDown(keyCode, event) || super.onKeyDown(keyCode, event)\n"
        "    }\n\n"
        "    override fun onKeyUp(keyCode: Int, event: KeyEvent?): Boolean {\n"
        "        return latinIMELegacy.onKeyUp(keyCode, event) || super.onKeyUp(keyCode, event)\n"
        "    }\n"
    )
    new_keys = (
        "    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {\n"
        "        when (hardwareKoreanInput.onKeyDown(keyCode, event, currentInputConnection)) {\n"
        "            HardwareKoreanInputController.Result.HANDLED -> return true\n"
        "            HardwareKoreanInputController.Result.SWITCHED_TO_KOREAN -> {\n"
        "                Toast.makeText(this, \"물리키보드: 한글\", Toast.LENGTH_SHORT).show()\n"
        "                return true\n"
        "            }\n"
        "            HardwareKoreanInputController.Result.SWITCHED_TO_ENGLISH -> {\n"
        "                Toast.makeText(this, \"물리키보드: 영문\", Toast.LENGTH_SHORT).show()\n"
        "                return true\n"
        "            }\n"
        "            HardwareKoreanInputController.Result.NOT_HANDLED -> Unit\n"
        "        }\n\n"
        "        return latinIMELegacy.onKeyDown(keyCode, event) || super.onKeyDown(keyCode, event)\n"
        "    }\n\n"
        "    override fun onKeyUp(keyCode: Int, event: KeyEvent?): Boolean {\n"
        "        if (hardwareKoreanInput.onKeyUp(keyCode, event)) return true\n"
        "        return latinIMELegacy.onKeyUp(keyCode, event) || super.onKeyUp(keyCode, event)\n"
        "    }\n"
    )
    text = replace_once(text, old_keys, new_keys, "hardware key overrides")

    path.write_text(text, encoding="utf-8")
    print(f"Patched {path}")


if __name__ == "__main__":
    main()
