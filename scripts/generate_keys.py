import starkbank

if __name__ == "__main__":
    private_key, public_key = starkbank.key.create('./stark-keys')
    print("PÚBLICA (colar no painel do Stark Bank):\n", public_key)
    print("\nPRIVADA (guardar em STARK_PRIVATE_KEY, nunca no repo):\n", private_key)
    