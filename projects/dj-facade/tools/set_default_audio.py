"""Set the Windows default playback device (all roles), e.g. to "Voicemeeter Input".

  python tools/set_default_audio.py                    # list playback devices
  python tools/set_default_audio.py "Voicemeeter Input"

Uses the same (undocumented but long-stable) IPolicyConfig interface as the Sound control panel.
"""

import sys
import warnings
from ctypes import HRESULT, c_int, c_void_p
from ctypes.wintypes import LPCWSTR

import comtypes
from comtypes import COMMETHOD, GUID, IUnknown
from pycaw.pycaw import AudioUtilities


class IPolicyConfig(IUnknown):
    _iid_ = GUID("{f8679f50-850a-41cf-9c72-430f290290c8}")
    _methods_ = [COMMETHOD([], HRESULT, f"Unused{i}") for i in range(10)] + [
        COMMETHOD([], HRESULT, "SetDefaultEndpoint", (["in"], LPCWSTR, "deviceId"), (["in"], c_int, "role")),
        COMMETHOD([], HRESULT, "SetEndpointVisibility", (["in"], LPCWSTR, "deviceId"), (["in"], c_int, "visible")),
    ]


CLSID_PolicyConfigClient = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")


def main():
    comtypes.CoInitialize()
    warnings.filterwarnings("ignore")          # pycaw warns about optional properties some drivers lack
    devices = [d for d in AudioUtilities.GetAllDevices()
               if "active" in str(d.state).lower() and d.id.startswith("{0.0.0.")]   # 0.0.0 = playback endpoints
    current = AudioUtilities.GetSpeakers().FriendlyName
    if len(sys.argv) < 2:
        for d in devices:
            print(("* " if d.FriendlyName == current else "  ") + d.FriendlyName)
        return
    want = sys.argv[1].lower()
    match = [d for d in devices if d.FriendlyName.lower().startswith(want)]
    if len(match) != 1:
        sys.exit(f"need exactly one match for {sys.argv[1]!r}, found: {[d.FriendlyName for d in match]}")
    policy = comtypes.CoCreateInstance(CLSID_PolicyConfigClient, IPolicyConfig, comtypes.CLSCTX_ALL)
    for role in (0, 1, 2):     # console, multimedia, communications
        policy.SetDefaultEndpoint(match[0].id, role)
    print(f"default playback: {current} -> {AudioUtilities.GetSpeakers().FriendlyName}")


if __name__ == "__main__":
    main()
