"""Drive CLI terminal prompts without logging test mnemonics."""
import errno
import os
import pty
import re
import select
import sys
import time


def run(command, phrases=(), timeout=700):
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(command[0], command)
    deadline = time.monotonic() + timeout
    output = b''
    answered = set()
    while time.monotonic() < deadline:
        if not select.select([fd], [], [], 1)[0]:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError as error:
            if error.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        output += chunk
        prompts = [('Type SAVED after storing every phrase offline:', 'SAVED'),
                   ('(yes/n)?', 'yes'), ('(y/n)?', 'yes')]
        prompts += [(f'Seed phrase {i}/{len(phrases)}:', phrase)
                    for i, phrase in enumerate(phrases, 1)]
        for prompt, answer in prompts:
            if prompt not in answered and prompt.encode() in output:
                os.write(fd, (answer + '\n').encode())
                answered.add(prompt)
    else:
        os.kill(pid, 15)
        raise TimeoutError('Terminal command timed out')
    _, status = os.waitpid(pid, 0)
    text = output.decode(errors='replace')
    text = re.sub(r'(?im)^.*seed phrase \d+:.*$', '[test mnemonic redacted]', text)
    for phrase in phrases:
        text = text.replace(phrase, '[test mnemonic redacted]')
    print(text)
    if os.waitstatus_to_exitcode(status):
        raise RuntimeError('Terminal command failed')


if __name__ == '__main__':
    run(sys.argv[1:])
