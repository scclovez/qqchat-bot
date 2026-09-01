Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 切换到脚本所在目录
shell.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)

' 定位真实的 pythonw.exe：优先 pythoncore-* 目录（避免 WindowsApps 的
' pythonw 别名在 GUI 模式下静默失败，导致"双击没反应"）
pythonwExe = ""
pythonDir = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Python"
If fso.FolderExists(pythonDir) Then
    For Each f In fso.GetFolder(pythonDir).SubFolders
        If LCase(Left(f.Name, 11)) = "pythoncore-" Then
            If fso.FileExists(f.Path & "\pythonw.exe") Then
                pythonwExe = f.Path & "\pythonw.exe"
                Exit For
            End If
        End If
    Next
End If

If pythonwExe <> "" Then
    shell.Run """" & pythonwExe & """ main.py --gui", 0, False
Else
    ' 兜底：PATH 里的 pythonw / python
    On Error Resume Next
    shell.Run "pythonw main.py --gui", 0, False
    If Err.Number <> 0 Then
        Err.Clear
        shell.Run "python main.py --gui", 0, False
    End If
    On Error Goto 0
End If
