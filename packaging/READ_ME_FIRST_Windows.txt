Jobapplier for Windows
======================

1. Right-click the downloaded .zip > "Extract All...". Keep the whole
   Jobapplier folder together (don't move Jobapplier.exe out of it).
2. Open the Jobapplier folder and double-click Jobapplier.exe.
3. The first time, Windows may say "Windows protected your PC".
   Click "More info", then "Run anyway". (The app isn't code-signed;
   this only happens once.)
4. Jobapplier opens in your web browser at http://localhost:8000.
   It opens on the Guide tab: read it, then follow the Setup tab
   from top to bottom. The Guide is there any time you need it.

You'll need a free Gemini API key: https://aistudio.google.com/apikey
(or, if you prefer, your own paid OpenAI or Anthropic API key).

Jobapplier fills in job applications in a browser window, but it never
clicks Submit. You check the form and press Submit yourself.
(Setup can switch to Claude Code or Codex, which fill in the whole
application by themselves; they also stop for you to submit.)

To close it: press "Quit" at the top right of the Jobapplier page.
Your data is stored in C:\Users\<you>\.applypilot
