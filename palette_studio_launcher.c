#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
  char executable_path[PATH_MAX];
  char launcher_path[PATH_MAX];
  char *last_slash;

  if (argc < 1 || !realpath(argv[0], executable_path)) {
    perror("Jade could not locate its launcher");
    return 1;
  }

  last_slash = strrchr(executable_path, '/');
  if (!last_slash) {
    fputs("Jade has an invalid app location.\n", stderr);
    return 1;
  }
  *last_slash = '\0';
  snprintf(launcher_path, sizeof(launcher_path), "%s/palette-studio-launcher", executable_path);
  execl("/bin/zsh", "zsh", launcher_path, (char *)NULL);
  perror("Jade could not start");
  return 1;
}
