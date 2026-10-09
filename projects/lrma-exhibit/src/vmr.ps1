# VoiceMeeter Remote API + Windows Core Audio helper, driven by src/apps/voicemeeter.js.
# Usage: powershell -File vmr.ps1 <command>   (JSON payload on stdin, JSON result on stdout)
# Commands: status | gain | set | load | restart | run | defaults | endpoints | list
param([Parameter(Mandatory = $true)][string]$Cmd)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$raw = [Console]::In.ReadToEnd()
$p = $null
if ($raw -and $raw.Trim()) { $p = $raw | ConvertFrom-Json }
$dllDir = "C:\Program Files (x86)\VB\Voicemeeter"
if ($p -and $p.dll_dir) { $dllDir = $p.dll_dir }
$dllPath = (Join-Path $dllDir "VoicemeeterRemote64.dll").Replace("\", "\\")   # absolute path baked into DllImport

$vmrSrc = @'
using System; using System.Runtime.InteropServices; using System.Text;
public static class VMR {
  const string D = "__DLL__";
  [DllImport(D)] public static extern int VBVMR_Login();
  [DllImport(D)] public static extern int VBVMR_Logout();
  [DllImport(D)] public static extern int VBVMR_RunVoicemeeter(int type);
  [DllImport(D)] public static extern int VBVMR_GetVoicemeeterType(ref int t);
  [DllImport(D)] public static extern int VBVMR_GetVoicemeeterVersion(ref int v);
  [DllImport(D)] public static extern int VBVMR_IsParametersDirty();
  [DllImport(D, CharSet=CharSet.Ansi)] public static extern int VBVMR_GetParameterFloat(string name, ref float v);
  [DllImport(D, CharSet=CharSet.Ansi)] public static extern int VBVMR_GetParameterStringA(string name, StringBuilder v);
  [DllImport(D, CharSet=CharSet.Ansi)] public static extern int VBVMR_SetParameters(string script);
  [DllImport(D)] public static extern int VBVMR_GetLevel(int type, int channel, ref float v);
  // Wrappers: keep ref/StringBuilder marshaling inside C# (PowerShell's [ref] binder crashed the DLL).
  public static int Type() { int t = 0; VBVMR_GetVoicemeeterType(ref t); return t; }
  public static int Version() { int v = 0; VBVMR_GetVoicemeeterVersion(ref v); return v; }
  public static float GetFloat(string name) { float v = 0; int rc = VBVMR_GetParameterFloat(name, ref v); return rc == 0 ? v : float.NaN; }
  public static string GetString(string name) { var sb = new StringBuilder(512); int rc = VBVMR_GetParameterStringA(name, sb); return rc == 0 ? sb.ToString() : ""; }
  public static float Level(int type, int channel) { float v = 0; VBVMR_GetLevel(type, channel, ref v); return v; }
}
'@
$caSrc = @'
using System; using System.Runtime.InteropServices;
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorCom {}
[ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int EnumAudioEndpoints(int flow, int mask, out IMMDeviceCollection c); int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice d); }
[ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceCollection { int GetCount(out int n); int Item(int i, out IMMDevice d); }
[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref Guid iid, int ctx, IntPtr p, out IntPtr o); int OpenPropertyStore(int access, out IPropertyStore ps); int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id); int GetState(out int s); }
[ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore { int GetCount(out int n); int GetAt(int i, out PROPERTYKEY k); int GetValue(ref PROPERTYKEY k, out PROPVARIANT v); }
[StructLayout(LayoutKind.Sequential)] public struct PROPERTYKEY { public Guid fmtid; public int pid; }
[StructLayout(LayoutKind.Explicit)] public struct PROPVARIANT { [FieldOffset(0)] public short vt; [FieldOffset(8)] public IntPtr p; }
// Undocumented but long-stable interface used by every audio-switcher tool (Windows 7 .. 11).
[ComImport, Guid("f8679f50-850a-41cf-9c72-430f290290c8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPolicyConfig {
  int GetMixFormat(string id, out IntPtr f); int GetDeviceFormat(string id, int d, out IntPtr f); int ResetDeviceFormat(string id);
  int SetDeviceFormat(string id, IntPtr a, IntPtr b); int GetProcessingPeriod(string id, int d, out long a, out long b);
  int SetProcessingPeriod(string id, IntPtr p); int GetShareMode(string id, IntPtr m); int SetShareMode(string id, IntPtr m);
  int GetPropertyValue(string id, int fx, ref PROPERTYKEY k, out PROPVARIANT v); int SetPropertyValue(string id, int fx, ref PROPERTYKEY k, ref PROPVARIANT v);
  int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string id, int role);
  int SetEndpointVisibility([MarshalAs(UnmanagedType.LPWStr)] string id, int visible);
}
[ComImport, Guid("870af99c-171d-4f9e-af0d-e63df40c2bc9")] class PolicyConfigClient {}
public class Endpoint { public string Id; public string Name; public int Flow; public int State; }
public static class CoreAudio {
  static string Name(IMMDevice d) { try { IPropertyStore ps; d.OpenPropertyStore(0, out ps); var k = new PROPERTYKEY { fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), pid = 14 }; PROPVARIANT v; ps.GetValue(ref k, out v); return v.p == IntPtr.Zero ? "" : Marshal.PtrToStringUni(v.p); } catch { return ""; } }
  public static string Default(int flow, int role) { var e = (IMMDeviceEnumerator)new MMDeviceEnumeratorCom(); IMMDevice d; if (e.GetDefaultAudioEndpoint(flow, role, out d) != 0) return ""; return Name(d); }
  public static Endpoint[] List(int flow, int mask) { var e = (IMMDeviceEnumerator)new MMDeviceEnumeratorCom(); IMMDeviceCollection c; e.EnumAudioEndpoints(flow, mask, out c); int n; c.GetCount(out n); var r = new Endpoint[n];
    for (int i = 0; i < n; i++) { IMMDevice d; c.Item(i, out d); string id; d.GetId(out id); int s; d.GetState(out s); r[i] = new Endpoint { Id = id, Name = Name(d), Flow = flow, State = s }; } return r; }
  public static int SetDefault(string id, int role) { return ((IPolicyConfig)new PolicyConfigClient()).SetDefaultEndpoint(id, role); }
  public static int SetVisible(string id, bool visible) { return ((IPolicyConfig)new PolicyConfigClient()).SetEndpointVisibility(id, visible ? 1 : 0); }
}
'@
Add-Type -TypeDefinition $vmrSrc.Replace("__DLL__", $dllPath)
Add-Type -TypeDefinition $caSrc

function F($name) { $v = [VMR]::GetFloat($name); if ([single]::IsNaN($v)) { $null } else { [math]::Round($v, 3) } }
function S($name) { [VMR]::GetString($name) }
function Lv($type, $ch) { [math]::Round([VMR]::Level($type, $ch), 3) }
function T($m) { if ($env:VMR_TRACE) { [Console]::Error.WriteLine("[vmr] $m") } }
T "types compiled; dll=$dllPath"

# Endpoint state bits: 1 active, 2 disabled, 4 not present, 8 unplugged
function AllEndpoints() { @([CoreAudio]::List(0, 3)) + @([CoreAudio]::List(1, 3)) }   # active + disabled
function FindEndpoint($flow, $name) {
  $all = [CoreAudio]::List($flow, 3)
  $exact = $all | Where-Object { $_.Name -eq $name } | Select-Object -First 1
  if ($exact) { return $exact }
  return $all | Where-Object { $_.Name.StartsWith($name, [StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1
}
function StateName($s) { switch ($s) { 1 { 'active' } 2 { 'disabled' } 4 { 'not-present' } 8 { 'unplugged' } default { "$s" } } }

function Login() {
  $rc = [VMR]::VBVMR_Login()
  if ($rc -lt 0) { throw "VBVMR_Login failed ($rc): is VoicemeeterRemote64.dll present in $dllDir ?" }
  Start-Sleep -Milliseconds 150
  [void][VMR]::VBVMR_IsParametersDirty()
  return $rc   # 0 = ok, 1 = logged in but Voicemeeter not running
}

function Status() {
  $rc = Login
  T "login rc=$rc"
  $t = [VMR]::Type()
  $v = [VMR]::Version()
  T "type=$t version=$v"
  $ver = "" + (($v -shr 24) -band 0xFF) + "." + (($v -shr 16) -band 0xFF) + "." + (($v -shr 8) -band 0xFF) + "." + ($v -band 0xFF)
  # Edition layout: [hw strips, virtual strips, hw buses, virtual buses]
  $layout = @{ 1 = @(2, 1, 1, 1); 2 = @(3, 2, 3, 2); 3 = @(5, 3, 5, 3) }[$t]
  if (-not $layout) { $layout = @(2, 1, 1, 1) }
  $strips = @(); $inCh = 0
  for ($i = 0; $i -lt ($layout[0] + $layout[1]); $i++) {
    $hw = $i -lt $layout[0]; $nch = 2; if (-not $hw) { $nch = 8 }
    $dev = ''; if ($hw) { $dev = S "Strip[$i].device.name" }   # virtual strips have no device parameter
    T "strip ${i} device=$dev"
    $mute = F "Strip[$i].mute"; T "strip ${i} mute=$mute"
    $a1 = F "Strip[$i].A1"; $b1 = F "Strip[$i].B1"; T "strip ${i} A1=$a1 B1=$b1"
    $strips += [ordered]@{ index = $i; kind = $(if ($hw) { 'hardware' } else { 'virtual' }); label = S "Strip[$i].label"; device = $dev;
      gain = F "Strip[$i].gain"; mute = $mute; A1 = $a1; B1 = $b1; level = @((Lv 0 $inCh), (Lv 0 ($inCh + 1))) }
    $inCh += $nch
  }
  $buses = @()
  for ($b = 0; $b -lt ($layout[2] + $layout[3]); $b++) {
    $hw = $b -lt $layout[2]
    $dev = ''; if ($hw) { $dev = S "Bus[$b].device.name" }
    T "bus ${b} device=$dev"
    $g = F "Bus[$b].gain"; $m = F "Bus[$b].mute"; T "bus ${b} gain=$g mute=$m"
    $buses += [ordered]@{ index = $b; name = $(if ($hw) { "A$($b + 1)" } else { "B$($b - $layout[2] + 1)" }); kind = $(if ($hw) { 'hardware' } else { 'virtual' }); device = $dev;
      gain = $g; mute = $m; level = @((Lv 3 ($b * 8)), (Lv 3 ($b * 8 + 1))) }
  }
  $sr = F 'Option.sr'
  [void][VMR]::VBVMR_Logout()   # nothing may touch the API after this: reads after logout crash the DLL
  return [ordered]@{
    running = ($rc -eq 0); edition = @{ 1 = 'Voicemeeter'; 2 = 'Banana'; 3 = 'Potato' }[$t]; version = $ver
    sampleRate = $sr; strips = $strips; buses = $buses
    windows = [ordered]@{ playback = [CoreAudio]::Default(0, 0); playbackCommunications = [CoreAudio]::Default(0, 2); recording = [CoreAudio]::Default(1, 0); recordingCommunications = [CoreAudio]::Default(1, 2) }
  }
}

function Gain($spec) {
  [void](Login)
  $name = "$($spec.param)"
  if ($null -ne $spec.db) {
    $rc = [VMR]::VBVMR_SetParameters("$name = $([math]::Round([double]$spec.db, 2))")
    if ($rc -ne 0) { [void][VMR]::VBVMR_Logout(); throw "VBVMR_SetParameters returned $rc for $name" }
    # Reads come from a cache the API only refreshes once it reports itself clean, so a
    # read straight after a write returns the PREVIOUS value. Drain the dirty flag first.
    for ($i = 0; $i -lt 40; $i++) {
      Start-Sleep -Milliseconds 15
      if ([VMR]::VBVMR_IsParametersDirty() -eq 0) { break }
    }
  }
  $db = F $name
  $mute = $null
  if ($spec.mute_param) { $mute = F "$($spec.mute_param)" }
  [void][VMR]::VBVMR_Logout()
  return [ordered]@{ param = $name; db = $db; mute = $mute }
}

function SetScript($script) {
  [void](Login)
  $rc = [VMR]::VBVMR_SetParameters($script)
  Start-Sleep -Milliseconds 300
  [void][VMR]::VBVMR_Logout()
  if ($rc -ne 0) { throw "VBVMR_SetParameters returned $rc for script: $script" }
}

function Defaults($spec) {
  $out = [ordered]@{}
  $map = @(@('playback', 0, @(0, 1)), @('playback_communications', 0, @(2)), @('recording', 1, @(0, 1)), @('recording_communications', 1, @(2)))
  foreach ($m in $map) {
    $key = $m[0]; $flow = $m[1]; $roles = $m[2]
    $want = $spec.$key
    if (-not $want) { continue }
    $ep = FindEndpoint $flow $want
    if (-not $ep) { throw "Audio endpoint not found for $key : '$want'" }
    if ($ep.State -ne 1) { [void][CoreAudio]::SetVisible($ep.Id, $true) }
    foreach ($r in $roles) { $rc = [CoreAudio]::SetDefault($ep.Id, $r); if ($rc -ne 0) { throw "SetDefaultEndpoint($($ep.Name), role $r) -> $rc" } }
    $out[$key] = $ep.Name
  }
  return $out
}

function Endpoints($spec) {
  $changed = @()
  foreach ($flowName in @('playback', 'recording')) {
    $flow = 0; if ($flowName -eq 'recording') { $flow = 1 }
    foreach ($name in @($spec.hide.$flowName)) {
      if (-not $name) { continue }
      $ep = FindEndpoint $flow $name
      if ($ep -and $ep.State -eq 1) { $rc = [CoreAudio]::SetVisible($ep.Id, $false); $changed += "hide $($ep.Name) -> $rc" }
    }
    foreach ($name in @($spec.show.$flowName)) {
      if (-not $name) { continue }
      $ep = FindEndpoint $flow $name
      if ($ep -and $ep.State -ne 1) { $rc = [CoreAudio]::SetVisible($ep.Id, $true); $changed += "show $($ep.Name) -> $rc" }
    }
  }
  return $changed
}

try {
  $result = switch ($Cmd) {
    'status'    { Status }
    'gain'      { Gain $p }
    'set'       { SetScript $p.script; [ordered]@{ ok = $true } }
    'load'      { SetScript ('Command.Load = "' + $p.file + '"'); [ordered]@{ ok = $true; loaded = $p.file } }
    'restart'   { SetScript 'Command.Restart = 1'; [ordered]@{ ok = $true } }
    'run'       { $rc = [VMR]::VBVMR_RunVoicemeeter([int]$p.type); [ordered]@{ ok = ($rc -eq 0); rc = $rc } }
    'defaults'  { Defaults $p }
    'endpoints' { [ordered]@{ changed = @(Endpoints $p) } }
    'list'      { @((AllEndpoints) | ForEach-Object { [ordered]@{ name = $_.Name; flow = $(if ($_.Flow -eq 0) { 'playback' } else { 'recording' }); state = StateName $_.State } }) }
    default     { throw "unknown command $Cmd" }
  }
  $result | ConvertTo-Json -Depth 6 -Compress
} catch {
  [ordered]@{ error = $_.Exception.Message } | ConvertTo-Json -Compress
  exit 1
}
