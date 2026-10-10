
from .compiler import detect_compiler, CompilerError
from .executor import execute_c, ExecutionError
from .installer import install_zig


TEST_CODE = r"""
#include <stdio.h>

int main(void) {
    printf("Hello from native C execution!\n");
    return 0;
}
"""


def main():
    print("Starting HackNC Laptop Agent")

    # Step 1: Detect OS and configure compiler
    try:
        compiler = detect_compiler()

    except CompilerError as error:
        print(f"Compiler setup failed:\n{error}")
        return 1

    print(f"Operating System: {compiler.system}")
    print(f"C Compiler: {compiler.name}")
    print(f"Compiler Path: {compiler.path}")

    # Step 2: Verify local C execution
    try:
        result = execute_c(
            source_code=TEST_CODE,
            compiler=compiler,
        )

    except (CompilerError, ExecutionError) as error:
        print(f"Execution failed: {error}")
        return 1

    print("\nExecution Result:")
    print(f"Exit Code: {result.returncode}")
    print(f"STDOUT: {result.stdout}")
    print(f"STDERR: {result.stderr}")

    if result.returncode != 0:
        return 1

    print("Agent initialized successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
