"""RLM Training Worker Subprocess.

Reads RLMTrainingConfig (JSON) from stdin.
Runs LoRA fine-tuning using HuggingFace PEFT.
Writes adapter to output_path.
"""

import json
import logging
import sys

# Configure basic logging for the subprocess
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("rlm_worker")


def main():
    try:
        # Read config from stdin
        config_json = sys.stdin.read()
        config = json.loads(config_json)

        base_model = config["base_model"]
        output_path = config["output_path"]
        dataset_path = config["dataset_path"]
        hf_model_id = config.get("hf_model_id")
        max_steps = config.get("max_steps", 100)
        lora_r = config.get("lora_r", 8)

        logger.info(
            f"Loaded config: model={base_model}, hf_model_id={hf_model_id}, "
            f"max_steps={max_steps}, lora_r={lora_r}"
        )

    except Exception as e:
        logger.error(f"Failed to read config from stdin: {e}")
        sys.exit(1)

    try:
        import torch
        from datasets import load_dataset
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments
    except ImportError:
        logger.error("Required ML libraries not installed. Run: pip install vibe-agent[rlm]")
        # For tests, we might want to just mock this and exit 0 if a specific flag is set
        # But for the worker, missing deps is a real error.
        sys.exit(1)

    try:
        # 1. Load Dataset
        # The dataset is JSONL format, each line is {"messages": [...]}
        dataset = load_dataset("json", data_files=dataset_path, split="train")
        logger.info(f"Loaded dataset with {len(dataset)} examples")

        # 2. Load Model & Tokenizer
        # We assume base_model is a HuggingFace hub id if it's not a local path.
        # Note: Ollama tags (e.g. qwen3:1.7b) need to be mapped to HF repos.
        # For simplicity in this implementation, we assume base_model is a valid HF id or local path.
        if hf_model_id is None:
            hf_model_id = base_model
            if "qwen" in base_model.lower():
                # A rough mapping if the user provided an Ollama tag
                hf_model_id = "Qwen/Qwen1.5-1.8B-Chat"

        logger.info(f"Loading tokenizer and model: {hf_model_id}")
        tokenizer = AutoTokenizer.from_pretrained(hf_model_id)
        if getattr(tokenizer, "pad_token", None) is None:
            tokenizer.pad_token = tokenizer.eos_token

        model = AutoModelForCausalLM.from_pretrained(
            hf_model_id,
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            device_map="auto"
        )

        # 3. Setup LoRA
        lora_config = LoraConfig(
            r=lora_r,
            lora_alpha=lora_r * 2,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM"
        )
        model = get_peft_model(model, lora_config)
        logger.info("Initialized PEFT/LoRA model")

        # 4. Preprocess dataset
        def format_chat(example):
            # Apply chat template
            text = tokenizer.apply_chat_template(example["messages"], tokenize=False, add_generation_prompt=False)
            return {"text": text}

        formatted_dataset = dataset.map(format_chat)

        def tokenize_function(examples):
            return tokenizer(examples["text"], truncation=True, max_length=512, padding="max_length")

        tokenized_dataset = formatted_dataset.map(tokenize_function, batched=True, remove_columns=formatted_dataset.column_names)

        # 5. Train
        training_args = TrainingArguments(
            output_dir=output_path,
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            max_steps=max_steps,
            learning_rate=2e-4,
            logging_steps=10,
            save_strategy="no", # We only save at the end
            optim="adamw_torch",
        )

        from transformers import DataCollatorForLanguageModeling
        trainer = Trainer(
            model=model,
            args=training_args,
            train_dataset=tokenized_dataset,
            data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False)
        )

        logger.info("Starting training loop...")
        trainer.train()

        # 6. Save Adapter
        logger.info(f"Saving LoRA adapter to {output_path}")
        model.save_pretrained(output_path)
        tokenizer.save_pretrained(output_path)

        logger.info("Training complete")
        sys.exit(0)

    except Exception as e:
        logger.exception(f"Training failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
