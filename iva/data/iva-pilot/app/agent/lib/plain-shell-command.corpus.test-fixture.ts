// Именованный корпус входов из четырёх рецензий L11. Пара означает
// [отказ самоперезапуска, отказ обхода расписания] после разрешённых v4.1 исключений.
export const REVIEW_CASES = [
  {
    name: "c1 grep pipeline",
    input: 'grep -nE "foo|batch|bar" agent/file.ts | head -20 | cut -d: -f1',
    expected: [false, false],
  },
  {
    name: "c1 grep and ls",
    input: "grep -oE '(FOO|BAR)=' .env; ls -la",
    expected: [false, false],
  },
  {
    name: "c1 environment grep",
    input: 'grep -n -E "EnvironmentFile|Environment=" unit.service',
    expected: [false, false],
  },
  { name: "c1 echo batch", input: 'echo "a|batch"', expected: [false, false] },
  { name: "c1 echo at", input: "echo 'a|at b'", expected: [false, false] },
  {
    name: "c1 getent",
    input: "getent hosts api.telegram.org",
    expected: [false, false],
  },
  {
    name: "c1 help sequence",
    input:
      'iva plugin sync --help 2>&1 | head -20; echo "=== doctor help ==="; iva doctor --help 2>&1 | head -20',
    expected: [false, false],
  },
  {
    name: "remaining sed pipeline",
    input: "journalctl | sed -n '1,20p' | grep -vE \"^\\s*(at now)\"",
    expected: [false, true],
  },
  {
    name: "remaining writing heredoc",
    input: "cat > /tmp/hl.py <<'EOF'\nat=parent.index(r)\nEOF",
    expected: [false, true],
  },
  {
    name: "remaining reading heredoc",
    input: "cat <<'EOF'\niva restart\nEOF",
    expected: [true, false],
  },
  {
    name: "remaining grep escaped pipe",
    input: 'grep -rl "a\\|b" .',
    expected: [false, false],
  },
  {
    name: "remaining git commit",
    input: "git commit -m 'fix: retries; at least once'",
    expected: [false, true],
  },
  {
    name: "remaining quoted iva",
    input: '"iva" restart --help',
    expected: [true, false],
  },
  {
    name: "review1 eval crontab",
    input: "eval crontab -",
    expected: [false, false],
  },
  {
    name: "review1 xargs crontab",
    input: "echo x | xargs crontab",
    expected: [false, false],
  },
  {
    name: "review1 time systemd-run",
    input: "time systemd-run --user true",
    expected: [false, false],
  },
  { name: "review1 bang at", input: "! at now", expected: [false, false] },
  {
    name: "review1 brace batch",
    input: "{ batch; }",
    expected: [false, false],
  },
  {
    name: "review1 split crontab",
    input: "cron\\\ntab -",
    expected: [false, false],
  },
  {
    name: "review1 split systemd-run",
    input: "systemd-\\\nrun --user true",
    expected: [false, false],
  },
  {
    name: "review1 redirect help",
    input: "iva restart > --help",
    expected: [true, false],
  },
  {
    name: "review1 stderr help",
    input: "iva restart 2>--help",
    expected: [true, false],
  },
  {
    name: "review1 stdin help",
    input: "iva restart < --help",
    expected: [true, false],
  },
  {
    name: "review1 append short help",
    input: "iva restart >> -h",
    expected: [true, false],
  },
  {
    name: "review1 leading redirect self",
    input: '>grep bash -c "echo x; iva restart"',
    expected: [true, false],
  },
  {
    name: "review1 leading redirect scheduler",
    input: ">grep eval 'a|batch'",
    expected: [false, true],
  },
  {
    name: "review1 escaped h in quotes",
    input: 'iva restart "--\\help"',
    expected: [true, false],
  },
  {
    name: "review1 escaped first dash in quotes",
    input: 'iva restart "\\--help"',
    expected: [true, false],
  },
  {
    name: "review1 escaped short help in quotes",
    input: 'iva restart "-\\h"',
    expected: [true, false],
  },
  {
    name: "review1 escaped p in quotes",
    input: 'iva restart "--hel\\p"',
    expected: [true, false],
  },
  {
    name: "review1 carriage return comment",
    input: "echo x\r# comment; iva restart",
    expected: [true, false],
  },
  {
    name: "review1 vertical tab comment",
    input: "echo x\u000b# comment; iva restart",
    expected: [true, false],
  },
  {
    name: "review1 form feed comment",
    input: "echo x\f# comment; iva restart",
    expected: [true, false],
  },
  {
    name: "review1 nbsp comment",
    input: "echo x # comment; iva restart",
    expected: [true, false],
  },
  {
    name: "review1 absolute grep",
    input: '/tmp/grep "echo x; iva restart"',
    expected: [true, false],
  },
  {
    name: "review1 relative grep",
    input: './grep "x|batch"',
    expected: [false, true],
  },
  {
    name: "review1 absolute echo",
    input: '/opt/bin/echo "x; crontab -"',
    expected: [false, true],
  },
  {
    name: "review1 neighbor sh",
    input: "grep 'a|batch' f; sh /tmp/x",
    expected: [false, true],
  },
  {
    name: "review1 neighbor bash",
    input: "grep 'a|batch' f; bash /tmp/x",
    expected: [false, true],
  },
  {
    name: "review1 neighbor python",
    input: "grep 'a|batch' f; python3 x.py",
    expected: [false, true],
  },
  {
    name: "review1 neighbor chmod",
    input: "grep 'a|batch' f; chmod +x x",
    expected: [false, true],
  },
  {
    name: "review1 neighbor source",
    input: "grep 'a|batch' f; source x",
    expected: [false, true],
  },
  {
    name: "review1 neighbor dot",
    input: "grep 'a|batch' f; . x",
    expected: [false, true],
  },
  {
    name: "review1 neighbor executable",
    input: "grep 'a|batch' f; ./x",
    expected: [false, true],
  },
  {
    name: "review1 input process substitution",
    input: "grep 'a|batch' <(echo x)",
    expected: [false, true],
  },
  {
    name: "review1 output process substitution",
    input: "grep 'a|batch' >(cat)",
    expected: [false, true],
  },
  {
    name: "review1 executing process substitution",
    input: "echo 'x; iva restart' >(sh)",
    expected: [true, false],
  },
  {
    name: "review1 xargs path with space",
    input: 'xargs -a "/tmp/input file" iva restart',
    expected: [false, false],
  },
  {
    name: "review1 xargs replacement with space",
    input: 'xargs -I "{} token" iva restart',
    expected: [false, false],
  },
  {
    name: "review1 time p",
    input: "time -p iva restart",
    expected: [false, false],
  },
  {
    name: "review1 time format",
    input: "time --format=x iva restart",
    expected: [false, false],
  },
  {
    name: "review1 getent into bash",
    input: "getent hosts api.telegram.org | bash",
    expected: [false, true],
  },
  {
    name: "review1 continued long help",
    input: "iva restart --he\\\nlp",
    expected: [true, false],
  },
  {
    name: "review1 continued short help",
    input: "iva restart -\\\nh",
    expected: [true, false],
  },
  {
    name: "review2 time redirect",
    input: "time -p iva restart >/dev/null",
    expected: [false, false],
  },
  {
    name: "review2 xargs redirect",
    input: "xargs crontab - >/dev/null",
    expected: [false, false],
  },
  {
    name: "review2 assignment leading redirect scheduler",
    input: "A=1 >/dev/null grep 'a|batch'",
    expected: [false, true],
  },
  {
    name: "review2 assignment leading redirect self",
    input: "X=1 2>&1 echo 'x; iva restart'",
    expected: [true, false],
  },
  {
    name: "review2 malformed fd scheduler",
    input: "echo 'a|batch' 2>&1x",
    expected: [false, true],
  },
  {
    name: "review2 malformed fd help",
    input: "iva restart --help 2>&1x",
    expected: [true, false],
  },
  {
    name: "review2 malformed fd comment",
    input: "echo ok 2>&1#note; iva restart",
    expected: [true, false],
  },
  {
    name: "review2 triple pipe",
    input: "grep 'a|batch' ||| echo ok",
    expected: [false, true],
  },
  {
    name: "review2 triple ampersand",
    input: "grep 'a|batch' &&& echo ok",
    expected: [false, true],
  },
  {
    name: "review2 triple semicolon",
    input: "grep 'a|batch' ;;; echo ok",
    expected: [false, true],
  },
  {
    name: "review2 mixed pipe ampersand",
    input: "grep 'a|batch' |&& echo ok",
    expected: [false, true],
  },
  {
    name: "review2 quoted assignment scheduler",
    input: "\"X=1\" grep 'a|batch'",
    expected: [false, true],
  },
  {
    name: "review2 quoted assignment self",
    input: "'X=1' echo 'x; iva restart'",
    expected: [true, false],
  },
  {
    name: "review2 continued self",
    input: "echo x\\\niva restart",
    expected: [true, false],
  },
  {
    name: "review2 continued crontab",
    input: "echo x\\\ncrontab -",
    expected: [false, true],
  },
  {
    name: "review2 continued batch",
    input: "echo x\\\nbatch",
    expected: [false, true],
  },
  {
    name: "review2 time reader path",
    input: "time echo ~/.iva-scripts/x",
    expected: [false, true],
  },
  {
    name: "review2 eval reader path",
    input: "eval printf ~/.config/systemd/user/x.timer",
    expected: [false, true],
  },
  {
    name: "review2 xargs reader path",
    input: "xargs echo ~/.iva-scripts/x",
    expected: [false, true],
  },
  {
    name: "review2 bang reader path",
    input: "! grep x ~/.iva-scripts/x",
    expected: [false, true],
  },
  {
    name: "review2 xargs sh self",
    input: "xargs sh -c 'iva restart'",
    expected: [false, false],
  },
  {
    name: "review2 xargs sh scheduler",
    input: "xargs sh -c 'crontab -'",
    expected: [false, false],
  },
  {
    name: "review2 time sh self",
    input: "time sh -c 'iva restart'",
    expected: [false, false],
  },
  {
    name: "review2 eval sh scheduler",
    input: "eval sh -c 'crontab -'",
    expected: [false, false],
  },
  {
    name: "review2 xargs sudo self",
    input: "xargs -n1 sudo iva restart",
    expected: [false, false],
  },
  {
    name: "review2 eval empty self",
    input: "eval '' iva restart",
    expected: [false, false],
  },
  {
    name: "review2 eval empty scheduler",
    input: "eval '' crontab -",
    expected: [false, false],
  },
  {
    name: "review2 heredoc stdout null self",
    input: "cat >/dev/null <<'EOF'\niva restart\nEOF",
    expected: [true, false],
  },
  {
    name: "review2 heredoc stderr null scheduler",
    input: "cat 2>/dev/null <<'EOF'\ncrontab -\nEOF",
    expected: [false, true],
  },
  {
    name: "review2 parameter after help",
    input: "iva restart --help; ${X}",
    expected: [true, false],
  },
  {
    name: "review2 parameter before help",
    input: "${CMD}; iva restart --help",
    expected: [true, false],
  },
  {
    name: "review2 fd before help",
    input: "iva restart 2>&1 --help",
    expected: [false, false],
  },
  {
    name: "review2 stdout fd before help",
    input: "iva restart >&2 --help",
    expected: [true, false],
  },
  {
    name: "review2 stdin fd before short help",
    input: "iva restart <&0 -h",
    expected: [true, false],
  },
  {
    name: "deepseek subshell producer self",
    input: "(echo 'x; iva restart') | bash",
    expected: [true, false],
  },
  {
    name: "deepseek subshell consumer self",
    input: "echo 'x; iva restart' | (bash)",
    expected: [true, false],
  },
  {
    name: "deepseek nested subshell self",
    input: "((echo 'x; iva restart')) | bash",
    expected: [true, false],
  },
  {
    name: "deepseek pipe stderr subshell",
    input: "echo 'x; iva restart' |& (sh)",
    expected: [true, false],
  },
  {
    name: "deepseek conditional subshell",
    input: "true && (echo 'x; iva restart') | bash",
    expected: [true, false],
  },
  {
    name: "deepseek subshell crontab",
    input: "(echo 'a; crontab -') | bash",
    expected: [false, true],
  },
  {
    name: "deepseek subshell at",
    input: "(echo 'a; at now') | bash",
    expected: [false, true],
  },
  {
    name: "deepseek subshell systemd-run",
    input: "(echo 'x; systemd-run --user true') | bash",
    expected: [false, true],
  },
  {
    name: "deepseek subshell pkill",
    input: "(echo 'x; pkill node') | bash",
    expected: [true, false],
  },
  {
    name: "deepseek process substitution redirect",
    input: "echo 'x; iva restart' > >(bash)",
    expected: [true, false],
  },
  {
    name: "deepseek direct pipe",
    input: "echo 'x; iva restart' | bash",
    expected: [true, false],
  },
  {
    name: "deepseek bash c scheduler",
    input: 'bash -c "echo a; crontab -"',
    expected: [false, true],
  },
  {
    name: "deepseek command substitution",
    input: 'echo "$(iva restart)"',
    expected: [true, false],
  },
  {
    name: "deepseek unquoted heredoc substitution",
    input: "cat <<EOF\n$(iva restart)\nEOF",
    expected: [true, false],
  },
  {
    name: "deepseek shell heredoc",
    input: "bash <<'EOF'\niva restart\nEOF",
    expected: [true, false],
  },
  {
    name: "deepseek ansi c quote",
    input: "echo $'a\\'; iva restart'",
    expected: [true, false],
  },
  {
    name: "deepseek hash in assignment",
    input: "x=a#b; iva restart",
    expected: [true, false],
  },
  {
    name: "deepseek redirect writer",
    input: 'echo > out "x|~/.iva-scripts/a"',
    expected: [false, true],
  },
  {
    name: "deepseek writing telegram heredoc",
    input: "cat > /tmp/x.sh <<'EOF'\ncurl https://api.telegram.org\nEOF",
    expected: [false, true],
  },
  {
    name: "deepseek bash both redirect",
    input: "echo 'x; iva restart' &> /dev/null",
    expected: [true, false],
  },
  {
    name: "deepseek bash append both redirect",
    input: "echo 'x; iva restart' &>> /dev/null",
    expected: [true, false],
  },
  {
    name: "deepseek here string",
    input: "cat <<< 'iva restart'",
    expected: [false, false],
  },
  {
    name: "deepseek unclosed quote",
    input: "echo 'x; iva restart",
    expected: [true, false],
  },
  {
    name: "deepseek eval self",
    input: "eval iva restart",
    expected: [false, false],
  },
  {
    name: "deepseek eval quoted self",
    input: 'eval "iva restart"',
    expected: [false, false],
  },
  {
    name: "deepseek xargs n1 self",
    input: "xargs -n1 iva restart",
    expected: [false, false],
  },
  {
    name: "deepseek xargs replacement self",
    input: "xargs -I{} iva restart",
    expected: [false, false],
  },
  {
    name: "deepseek xargs lines self",
    input: "xargs -L 1 iva restart",
    expected: [false, false],
  },
  {
    name: "deepseek xargs parallel self",
    input: "xargs -P2 iva restart",
    expected: [false, false],
  },
  {
    name: "deepseek xargs delimiter self",
    input: "xargs -d : iva restart",
    expected: [false, false],
  },
  {
    name: "deepseek xargs eof self",
    input: "xargs -EEOF iva restart",
    expected: [false, false],
  },
  {
    name: "deepseek xargs size self",
    input: "xargs -s 1024 iva restart",
    expected: [false, false],
  },
  {
    name: "deepseek xargs file self",
    input: "xargs -a/tmp/input iva restart",
    expected: [false, false],
  },
  {
    name: "deepseek time self",
    input: "time iva restart",
    expected: [false, false],
  },
  {
    name: "deepseek home iva self",
    input: "$HOME/.local/bin/iva restart",
    expected: [false, false],
  },
  {
    name: "deepseek split iva self",
    input: "iv\\\na restart",
    expected: [false, false],
  },
  {
    name: "deepseek split systemctl self",
    input: "sys\\\ntemctl --user restart iva",
    expected: [false, false],
  },
  {
    name: "deepseek grep eval data",
    input: "grep -n 'eval' f",
    expected: [false, false],
  },
  {
    name: "deepseek echo xargs data",
    input: "echo 'xargs iva restart'",
    expected: [false, false],
  },
  {
    name: "deepseek heredoc hardening data",
    input: "cat <<'EOF'\ntime iva restart\neval iva restart\nEOF",
    expected: [false, false],
  },
  {
    name: "deepseek help doctor",
    input: "iva doctor --help",
    expected: [false, false],
  },
  {
    name: "deepseek help quoted value",
    input: "iva restart '--help'",
    expected: [false, false],
  },
  {
    name: "deepseek help concatenated",
    input: 'iva restart --he"lp"',
    expected: [false, false],
  },
  {
    name: "deepseek help plugin short",
    input: "iva plugin sync -h",
    expected: [false, false],
  },
  {
    name: "deepseek help with space",
    input: 'iva restart "--help me"',
    expected: [true, false],
  },
  {
    name: "deepseek help suffix",
    input: 'iva restart "x "--help',
    expected: [true, false],
  },
  {
    name: "deepseek help escaped space",
    input: "iva restart --help\\ x",
    expected: [true, false],
  },
  {
    name: "deepseek bash help argument",
    input: 'bash -c "iva restart" --help',
    expected: [true, false],
  },
  {
    name: "deepseek node help argument",
    input: "node bin/iva.mjs restart --help",
    expected: [true, false],
  },
  {
    name: "deepseek help in substitution",
    input: "x=$(iva restart --help)",
    expected: [true, false],
  },
  {
    name: "deepseek help neighboring segment",
    input: "grep --help f; iva restart",
    expected: [true, false],
  },
  {
    name: "v3 printf v self",
    input: "printf -v 'x[$(iva restart)]' x",
    expected: [true, false],
  },
  {
    name: "v3 printf v scheduler",
    input: "printf -v 'x[$(systemd-run --user --on-active=1h true)]' x",
    expected: [false, true],
  },
  {
    name: "v3 sort output heredoc",
    input: "sort -o /tmp/iva-l11.sh <<'EOF'\n#!/bin/sh\niva restart\nEOF",
    expected: [true, false],
  },
  {
    name: "v3 uniq output heredoc",
    input: "uniq - /tmp/iva-l11.sh <<'EOF'\n#!/bin/sh\niva restart\nEOF",
    expected: [true, false],
  },
  {
    name: "v3 file compile",
    input: "file -C -m /tmp/custom.magic",
    expected: [false, false],
  },
  {
    name: "v3 rg pre",
    input: "rg --pre=/tmp/helper pattern input",
    expected: [false, false],
  },
  {
    name: "v3 sort compress",
    input: "sort --comp=/tmp/helper --buffer-size=1K large-input",
    expected: [false, false],
  },
  {
    name: "v3 file external decompress",
    input: "file -S -z archive.gz",
    expected: [false, false],
  },
  {
    name: "v3 sanitized printf v",
    input: "printf -v 'x' x",
    expected: [false, false],
  },
  {
    name: "v3 sort output simple",
    input: "sort -o /tmp/x input",
    expected: [false, false],
  },
  { name: "v3 watchdog", input: "cat /dev/watchdog", expected: [false, false] },
  {
    name: "v3 tail follow",
    input: "tail -F /tmp/missing",
    expected: [false, false],
  },
  { name: "v3 zero reader", input: "cat /dev/zero", expected: [false, false] },
  { name: "v3 zero sort", input: "sort /dev/zero", expected: [false, false] },
  { name: "v3 jq file", input: "jq . input.json", expected: [false, false] },
  {
    name: "v3 jq rawfile",
    input: "jq --rawfile name file '. + $name' input.json",
    expected: [false, false],
  },
  {
    name: "v3 file magic",
    input: "file -m magic input",
    expected: [false, false],
  },
  {
    name: "v3 echo escapes",
    input: "echo -e 'a\\nb'",
    expected: [false, false],
  },
  { name: "v3 regular cat", input: "cat /tmp/input", expected: [false, false] },
  {
    name: "v3 uniq output",
    input: "uniq INPUT OUTPUT",
    expected: [false, false],
  },
  {
    name: "v3 sort two inputs",
    input: "sort INPUT OUTPUT",
    expected: [false, false],
  },
  {
    name: "v3 cut two inputs",
    input: "cut -d: -f1 INPUT OUTPUT",
    expected: [false, false],
  },
  { name: "v3 stat", input: "stat input", expected: [false, false] },
  {
    name: "v3 basename",
    input: "basename /tmp/input",
    expected: [false, false],
  },
  { name: "v3 dirname", input: "dirname /tmp/input", expected: [false, false] },
  { name: "v3 realpath", input: "realpath input", expected: [false, false] },
  { name: "v3 readlink", input: "readlink input", expected: [false, false] },
  {
    name: "specv1 F3 exact",
    input:
      "grep -oE '^(GEMINI_API_KEY|GOOGLE_API_KEY|OPENROUTER_API_KEY)=' ~/iva/.env ~/.iva-scripts/*.env 2>/dev/null; ls ~/.iva-scripts | head -50",
    expected: [false, false],
  },
  {
    name: "specv1 F4 exact",
    input:
      'grep -n -E "EnvironmentFile|Environment=" ~/.config/systemd/user/x.service',
    expected: [false, false],
  },
  {
    name: "specv1 F5 exact",
    input:
      "cat > /tmp/x/hl.py <<'EOF'\nimport sys\nparent=r.getparent(); at=parent.index(r)\nEOF",
    expected: [false, true],
  },
  {
    name: "specv1 F6 exact",
    input:
      'grep -rl "get-messages-json\\|get_messages_json" --include=*.py scripts .iva-scripts 2>/dev/null | head',
    expected: [false, true],
  },
  {
    name: "specv1 F8 sed exact",
    input:
      "iva doctor --help 2>&1 | sed 's/\\x1b\\[[0-9;]*m//g' | head; grep -n \"doctor\" docs/cli.md | head",
    expected: [true, false],
  },
  {
    name: "specv1 T1 exact",
    input: "iva jobs 2>&1 | tail -40; iva doctor 2>&1 | tail -60",
    expected: [true, false],
  },
  {
    name: "specv1 T2 exact",
    input: "timeout 120 iva plugin sync 2>&1 | tail -25",
    expected: [true, false],
  },
  {
    name: "fact7 cat heredoc pipe sh",
    input: "cat <<'EOF' | sh\ncrontab -\nEOF",
    expected: [false, true],
  },
  {
    name: "fact7 brace pipe bash",
    input: "{ echo 'a; iva restart'; } | bash",
    expected: [true, false],
  },
  {
    name: "fact7 for pipe bash",
    input: "for i in 1; do echo 'a; at now'; done | bash",
    expected: [false, true],
  },
  {
    name: "fact7 bash c crontab",
    input: 'bash -c "grep x; crontab -"',
    expected: [false, true],
  },
  {
    name: "fact7 sh c self",
    input: "sh -c 'echo a; iva restart'",
    expected: [true, false],
  },
  {
    name: "fact7 eval grep",
    input: "eval grep 'a|batch' f",
    expected: [false, true],
  },
  {
    name: "fact7 backtick crontab",
    input: 'echo "`crontab -`"',
    expected: [false, true],
  },
  {
    name: "fact7 python heredoc",
    input: "python3 - <<'EOF'\nsubprocess.run('iva restart', shell=True)\nEOF",
    expected: [true, false],
  },
  {
    name: "fact7 hash parameter",
    input: "echo $#; iva restart",
    expected: [true, false],
  },
  {
    name: "fact7 comment pipe batch",
    input: "ls # (at 5) | batch",
    expected: [false, false],
  },
  {
    name: "fact7 comment at",
    input: "ls # later: at now",
    expected: [false, false],
  },
  {
    name: "fact7 quoted greater",
    input: "grep -rn 'x > y' ~/.iva-scripts",
    expected: [false, false],
  },
  {
    name: "fact7 printing telegram heredoc",
    input: "cat <<'EOF'\ncurl https://api.telegram.org/bot1/sendMessage\nEOF",
    expected: [false, true],
  },
  {
    name: "fact7 rg data",
    input: "rg -n 'a|batch' agent/",
    expected: [false, true],
  },
  {
    name: "fact7 jq data",
    input: "jq '.items[] | at' f.json",
    expected: [false, true],
  },
  {
    name: "fact7 sed data",
    input: "sed -n '/a|batch/p' f",
    expected: [false, true],
  },
  {
    name: "fact7 awk data",
    input: "awk '/a|batch/' f",
    expected: [false, true],
  },
] as const;
