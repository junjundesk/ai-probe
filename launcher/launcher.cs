using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

internal static class Launcher
{
    private const string AppFileName = "app.py";

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(IntPtr hWnd, string text, string caption, uint type);

    private static int Main()
    {
        string exeDir = AppDomain.CurrentDomain.BaseDirectory;
        string appPy = FindAppPy(exeDir);
        if (appPy == null)
        {
            ShowError("找不到 " + AppFileName + "，请把启动器放在 AI Probe 项目目录下。");
            return 1;
        }

        string workingDir = Path.GetDirectoryName(appPy);
        string python = FindPython(exeDir, workingDir);
        if (python == null)
        {
            ShowError(
                "未找到可用的 Python。请安装 Python 3 并勾选 Add python.exe to PATH，"
                + "或设置环境变量 AI_PROBE_PYTHON 指向 python.exe / pythonw.exe。"
            );
            return 1;
        }

        try
        {
            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = python,
                Arguments = BuildArguments(appPy, python),
                WorkingDirectory = workingDir,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using (Process process = Process.Start(psi))
            {
                process.WaitForExit();
                if (process.ExitCode != 0)
                {
                    ShowError("AI Probe 启动失败，退出码：" + process.ExitCode);
                    return process.ExitCode;
                }
            }
        }
        catch (Exception ex)
        {
            ShowError("无法启动 AI Probe：" + ex.Message);
            return 1;
        }

        return 0;
    }

    private static string FindAppPy(string exeDir)
    {
        string dir = exeDir;
        for (int i = 0; i < 4; i++)
        {
            string candidate = Path.Combine(dir, AppFileName);
            if (File.Exists(candidate))
            {
                return candidate;
            }

            string parent = Path.GetDirectoryName(dir);
            if (parent == null || parent == dir)
            {
                break;
            }
            dir = parent;
        }
        return null;
    }

    private static string FindPython(string exeDir, string appDir)
    {
        string configured = Environment.GetEnvironmentVariable("AI_PROBE_PYTHON");
        if (!string.IsNullOrEmpty(configured) && File.Exists(configured))
        {
            return configured;
        }

        string[] localCandidates =
        {
            Path.Combine(exeDir, "pythonw.exe"),
            Path.Combine(exeDir, "python.exe"),
            Path.Combine(exeDir, ".venv", "Scripts", "pythonw.exe"),
            Path.Combine(exeDir, ".venv", "Scripts", "python.exe"),
            Path.Combine(appDir, "pythonw.exe"),
            Path.Combine(appDir, "python.exe"),
            Path.Combine(appDir, ".venv", "Scripts", "pythonw.exe"),
            Path.Combine(appDir, ".venv", "Scripts", "python.exe"),
        };
        foreach (string candidate in localCandidates)
        {
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }

        string pythonw = FindOnPath("pythonw.exe");
        if (pythonw != null)
        {
            return pythonw;
        }

        string python = FindOnPath("python.exe");
        if (python != null)
        {
            return python;
        }

        string py = FindOnPath("py.exe");
        return py;
    }

    private static string FindOnPath(string fileName)
    {
        string pathVar = Environment.GetEnvironmentVariable("PATH");
        if (string.IsNullOrEmpty(pathVar))
        {
            return null;
        }

        foreach (string rawDir in pathVar.Split(Path.PathSeparator))
        {
            if (string.IsNullOrWhiteSpace(rawDir))
            {
                continue;
            }

            string dir = rawDir.Trim('"');
            string candidate = Path.Combine(dir, fileName);
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }
        return null;
    }

    private static string BuildArguments(string appPy, string python)
    {
        string[] args = Environment.GetCommandLineArgs();
        StringBuilder sb = new StringBuilder();

        string fileName = Path.GetFileName(python);
        if (string.Equals(fileName, "py.exe", StringComparison.OrdinalIgnoreCase))
        {
            sb.Append("-3 ");
        }

        sb.Append('"').Append(appPy).Append('"');
        for (int i = 1; i < args.Length; i++)
        {
            sb.Append(' ').Append(Quote(args[i]));
        }
        return sb.ToString();
    }

    private static string Quote(string value)
    {
        if (value.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
        {
            return value;
        }
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    private static void ShowError(string text)
    {
        MessageBoxW(IntPtr.Zero, text, "AI Probe 启动器", 0x10);
    }
}
