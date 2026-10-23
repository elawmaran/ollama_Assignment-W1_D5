
import ollama

MODEL_NAME = "qwen2.5:3b"


def chat():
    print("\n" + "=" * 45)
    print("       MY LOCAL AI CHATBOT")
    print("=" * 45)
    print(f"Model: {MODEL_NAME}")
    print("Type 'exit' or 'quit' to stop.")
    print("-" * 45)

    messages = []

    while True:
        user_input = input("\nYou: ").strip()

        if user_input.lower() in ["exit", "quit"]:
            print("AI: Goodbye!")
            break

        if not user_input:
            continue

        messages.append({
            "role": "user",
            "content": user_input
        })

        try:
            print("\nAI: ", end="", flush=True)
            response = ollama.chat(
                model=MODEL_NAME,
                messages=messages,
                stream=True
            )

            answer = ""

            for chunk in response:
                text = chunk["message"]["content"]
                print(text, end="", flush=True)
                answer += text

            print()

            messages.append({
                "role": "assistant",
                "content": answer
            })

        except Exception as error:
            print(f"\nError: {error}")
            print("Check that Ollama is running and the model is installed.")


if __name__ == "__main__":
    chat()
