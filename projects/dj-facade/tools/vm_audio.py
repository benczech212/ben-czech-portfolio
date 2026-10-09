"""Read / set Voicemeeter routing for feeding the facade's Pixelblaze sensor board.

The Pixelblaze sensor board takes line audio from the PC's rear 3.5 mm jack
("Speakers (High Definition Audio Device)"). With Voicemeeter Banana that jack is hardware
out A2, alongside the normal speakers on A1; the Windows/app audio strip feeds both.

  python tools/vm_audio.py status
  python tools/vm_audio.py route      # A2 = rear jack, app audio -> A1 + A2 + B2, mics off A2/B2
"""

import ctypes
import sys
import time

DLL = r"C:\Program Files (x86)\VB\Voicemeeter\VoicemeeterRemote64.dll"
REAR_JACK = "Speakers (High Definition Audio Device)"
MAIN_SPEAKERS = "Speakers (3- USB  AUDIO  )"   # A1 in the old standard-Voicemeeter setup
MAIN_MIC = "Microphone (2- Yeti Orb)"           # first hardware input in the old setup
KINDS = {1: "Voicemeeter", 2: "Banana", 3: "Potato"}
LAYOUT = {  # kind: (hardware input strips, virtual input strips, hardware out buses, virtual out buses)
    1: (2, 1, 1, 1),
    2: (3, 2, 3, 2),
    3: (5, 3, 5, 3),
}

vm = ctypes.WinDLL(DLL)


def sp(name):
    buf = ctypes.create_unicode_buffer(512)
    return buf.value if vm.VBVMR_GetParameterStringW(name.encode(), buf) == 0 else None


def fp(name):
    f = ctypes.c_float()
    return f.value if vm.VBVMR_GetParameterFloat(name.encode(), ctypes.byref(f)) == 0 else None


def refresh():
    for _ in range(20):            # let the parameter cache catch up
        vm.VBVMR_IsParametersDirty()
        time.sleep(0.05)


def kind():
    t = ctypes.c_long()
    vm.VBVMR_GetVoicemeeterType(ctypes.byref(t))
    return t.value


def outputs():
    out = []
    for i in range(vm.VBVMR_Output_GetDeviceNumber()):
        typ = ctypes.c_long()
        name = ctypes.create_unicode_buffer(256)
        hw = ctypes.create_unicode_buffer(256)
        vm.VBVMR_Output_GetDeviceDescW(i, ctypes.byref(typ), name, hw)
        out.append(({1: "mme", 3: "wdm", 4: "ks", 5: "asio"}.get(typ.value, "?"), name.value))
    return out


def status():
    k = kind()
    hw_in, v_in, hw_out, v_out = LAYOUT[k]
    print(f"Voicemeeter {KINDS[k]}")
    print("-- hardware outputs --")
    for b in range(hw_out):
        print(f"  A{b + 1}: {sp(f'Bus[{b}].device.name') or '(none)'}   mute={int(fp(f'Bus[{b}].Mute'))} gain={fp(f'Bus[{b}].Gain'):.1f} dB")
    print("-- inputs -> outputs --")
    for s in range(hw_in + v_in):
        routes = [f"A{i + 1}" for i in range(hw_out) if fp(f"Strip[{s}].A{i + 1}") == 1]
        routes += [f"B{i + 1}" for i in range(v_out) if fp(f"Strip[{s}].B{i + 1}") == 1]
        what = sp(f"Strip[{s}].device.name") if s < hw_in else ("Voicemeeter Input (Windows/apps)" if s == hw_in else "Voicemeeter AUX Input")
        print(f"  Strip[{s}] {what or '(no device)':45s} -> {' '.join(routes) or '-'}   mute={int(fp(f'Strip[{s}].Mute'))}")
    return k


def route():
    k = kind()
    if k == 1:
        sys.exit("Standard Voicemeeter has one hardware output; this needs Banana or Potato.")
    hw_in, v_in, hw_out, v_out = LAYOUT[k]
    if ("wdm", REAR_JACK) not in outputs():
        sys.exit(f"'{REAR_JACK}' is not available as a WDM output")
    script = [f'Bus[1].device.wdm="{REAR_JACK}"', "Bus[1].Mute=0", "Bus[1].Gain=0"]
    if not sp("Bus[0].device.name") and ("wdm", MAIN_SPEAKERS) in outputs():
        script.append(f'Bus[0].device.wdm="{MAIN_SPEAKERS}"')      # fresh install: the usual speakers on A1
    if not sp("Strip[0].device.name"):
        script.append(f'Strip[0].device.wdm="{MAIN_MIC}"')
    for s in range(hw_in, hw_in + v_in):          # Windows/app audio + AUX -> speakers, the jack, and B2
        script += [f"Strip[{s}].A1=1", f"Strip[{s}].A2=1", f"Strip[{s}].B2=1"]   # (B2 = clean tap for recordings)
    for s in range(hw_in):                         # keep microphones out of the facade and recordings
        script += [f"Strip[{s}].A2=0", f"Strip[{s}].B2=0"]
    r = vm.VBVMR_SetParameters(";".join(script).encode() + b";")
    if r != 0:
        sys.exit(f"Voicemeeter rejected the settings (code {r})")
    print("routing set")


def main():
    if vm.VBVMR_Login() < 0:
        sys.exit("could not connect to Voicemeeter (is it running?)")
    try:
        refresh()
        if len(sys.argv) > 1 and sys.argv[1] == "route":
            route()
            time.sleep(1)
            refresh()
        status()
    finally:
        vm.VBVMR_Logout()


if __name__ == "__main__":
    main()
