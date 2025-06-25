from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
from typing import Iterable, List, Dict, Union, Optional, Tuple, Callable, cast
from .core import (
    Chunk,
    calculate_tokens,
    DEFAULT_AI_MODEL,
    HOST_URL,
    THEPIPE_API_KEY,
)
from .scraper import scrape_url, scrape_file
from .chunker import (
    chunk_by_page,
    chunk_by_document,
    chunk_by_section,
    chunk_semantic,
    chunk_by_keywords,
    chunk_by_length,
    chunk_agentic,
)
import requests
import os
from openai import OpenAI
from openai.types.chat.chat_completion_message_param import ChatCompletionMessageParam

DEFAULT_EXTRACTION_PROMPT = "Extract all the information from the given document according to the following schema: {schema}. Immediately return valid JSON formatted data. If there is missing data, you may use null, but always fill in every column as best you can. Always immediately return valid JSON. You must extract ALL the information available in the entire document."

def extract_json_from_response(llm_response: str) -> Union[Dict, List[Dict], None]:
    def clean_response_text(llm_response: str) -> str:
        return llm_response.encode("utf-8", "ignore").decode("utf-8").strip()

    code_block_pattern = r"^```(?:json)?\s*([\s\S]*?)\s*```$"
    match = re.match(code_block_pattern, llm_response, re.MULTILINE | re.DOTALL)
    if match:
        llm_response = match.group(1)
    llm_response = clean_response_text(llm_response)

    try:
        parsed_json = json.loads(llm_response)
        return parsed_json
    except json.JSONDecodeError:
        json_pattern = r"($$[\s\S]*$$|\{[\s\S]*\})"
        match = re.search(json_pattern, llm_response)
        if match:
            try:
                parsed_json = json.loads(match.group(1))
                return parsed_json
            except json.JSONDecodeError:
                pass

    objects = re.findall(r"\{[^{}]*\}", llm_response)
    if objects:
        valid_objects = []
        for obj in objects:
            try:
                obj = obj.replace("\\", "").replace('\\"', '"')
                valid_objects.append(json.loads(obj))
            except json.JSONDecodeError:
                continue
        if valid_objects:
            return valid_objects if len(valid_objects) > 1 else valid_objects[0]
    print(f"[thepipe] Failed to extract valid JSON from LLM response: {llm_response}")
    return None

def extract_from_chunk(
    chunk: Chunk,
    chunk_index: int,
    schema: str,
    ai_model: str,
    source: str,
    multiple_extractions: bool,
    extraction_prompt: str,
    host_images: bool,
    openai_client: OpenAI,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
) -> Tuple[Dict, int]:
    response_dict = {"chunk_index": chunk_index, "source": source}
    tokens_used = 0
    try:
        # Use provided client or create one if needed
        if openai_client is None:
            # Configure OpenAI client with provided credentials
            client_args = {
                "api_key": api_key or os.environ.get("OPENAI_API_KEY", os.environ.get("LLM_SERVER_API_KEY")),
            }
            
            # Set API base if provided
            if api_base:
                client_args["base_url"] = api_base
            elif os.environ.get("LLM_SERVER_BASE_URL"):
                client_args["base_url"] = os.environ.get("LLM_SERVER_BASE_URL")
                
            openai_client = OpenAI(**client_args)

        corrected_extraction_prompt = extraction_prompt.replace("{schema}", schema)
        if multiple_extractions:
            corrected_extraction_prompt += """\nIf there are multiple extractions, return each JSON dictionary in a list under the key "extraction". The list should contain each extraction dict (according to the schema) and the entire list should be set to the "extraction" key. Immediately return this extraction JSON object with the "extraction" key mapping to a list containing all the extracted data."""
        else:
            corrected_extraction_prompt += (
                """\nImmediately return the JSON dictionary."""
            )

        messages = [
            chunk.to_message(host_images=host_images),
            {
                "role": "user",
                "content": corrected_extraction_prompt,
            },
        ]

        response = openai_client.chat.completions.create(
            model=ai_model,
            messages=cast(Iterable[ChatCompletionMessageParam], messages),
            response_format={"type": "json_object"},
            temperature=0,
        )
        llm_response = response.choices[0].message.content
        if not llm_response:
            raise Exception(
                f"Failed to receive a message content from LLM Response: {response}"
            )
        input_tokens = calculate_tokens([chunk])
        output_tokens = calculate_tokens([Chunk(text=llm_response)])
        tokens_used += input_tokens + output_tokens
        
        # Process the response
        llm_response_dict = extract_json_from_response(llm_response)
        if llm_response_dict:
            if multiple_extractions:
                if (
                    isinstance(llm_response_dict, dict)
                    and "extraction" in llm_response_dict
                ):
                    response_dict["extraction"] = llm_response_dict["extraction"]
                elif isinstance(llm_response_dict, list):
                    response_dict["extraction"] = llm_response_dict
                else:
                    response_dict["extraction"] = [llm_response_dict]
            else:
                if isinstance(llm_response_dict, dict):
                    response_dict.update(llm_response_dict)
                elif isinstance(llm_response_dict, list):
                    response_dict["error"] = (
                        f"Expected a single JSON object but received a list: {llm_response_dict}. Try enabling multiple extractions."
                    )
                else:
                    response_dict["error"] = (
                        f"Invalid JSON structure in LLM response: {llm_response_dict}"
                    )
        else:
            response_dict["error"] = (
                f"Failed to extract valid JSON from LLM response: {llm_response}"
            )
            
        # If not using multiple extractions, ensure all schema keys are present
        if not multiple_extractions:
            schema_keys = (
                json.loads(schema).keys() if isinstance(schema, str) else schema.keys()
            )
            for key in schema_keys:
                if key not in response_dict:
                    response_dict[key] = None
    except Exception as e:
        response_dict = {"chunk_index": chunk_index, "source": source, "error": str(e)}
    return response_dict, tokens_used

def extract(
    chunks: List[Chunk],
    schema: Union[str, Dict],
    ai_model: Optional[str] = "openai/gpt-4o-mini",
    multiple_extractions: Optional[bool] = False,
    extraction_prompt: Optional[str] = DEFAULT_EXTRACTION_PROMPT,
    host_images: Optional[bool] = False,
    options: Optional[Dict[str, Any]] = None,
    openai_client: Optional[OpenAI] = None,
) -> Tuple[List[Dict], int]:
    print(
        f"[thepipe] Extract functions will be deprecated in future versions. See the README for more information"
    )
    
    if isinstance(schema, dict):
        schema = json.dumps(schema)

    results = []
    total_tokens_used = 0

    # Assign default values if needed
    if ai_model is None:
        ai_model = DEFAULT_AI_MODEL
    if extraction_prompt is None:
        extraction_prompt = DEFAULT_EXTRACTION_PROMPT
    if host_images is None:
        host_images = False
    if multiple_extractions is None:
        multiple_extractions = False
        
    # Get LLM configuration from options if provided
    llm_config = options.get("llm_extractor", {}) if options else {}
    api_key = llm_config.get("api_key")
    api_base = llm_config.get("api_base")
    model_name = llm_config.get("model")
    
    # Override model if specified in llm_config
    if model_name:
        ai_model = model_name

    n_threads = (os.cpu_count() or 1) * 2
    with ThreadPoolExecutor(max_workers=n_threads) as executor:
        future_to_chunk = {
            executor.submit(
                extract_from_chunk,
                chunk=chunk,
                chunk_index=i,
                schema=schema,
                ai_model=ai_model,
                source=chunk.path or "",
                multiple_extractions=multiple_extractions,
                extraction_prompt=extraction_prompt,
                host_images=host_images,
                openai_client=openai_client,
                api_key=api_key,
                api_base=api_base
            ): i
            for i, chunk in enumerate(chunks)
        }

        for future in as_completed(future_to_chunk):
            try:
                result, tokens_used = future.result()
                results.append(result)
                total_tokens_used += tokens_used
            except Exception as e:
                chunk_index = future_to_chunk[future]
                results.append(
                    {
                        "chunk_index": chunk_index,
                        "source": chunks[chunk_index].path,
                        "error": str(e),
                    }
                )

    results.sort(key=lambda x: x["chunk_index"])
    return results, total_tokens_used

def extract_from_url(
    url: str,
    schema: Union[str, Dict],
    ai_model: str = "google/gemma-2-9b-it",
    multiple_extractions: bool = False,
    extraction_prompt: str = DEFAULT_EXTRACTION_PROMPT,
    host_images: bool = False,
    text_only: bool = False,
    ai_extraction: bool = False,
    verbose: bool = False,
    chunking_method: Optional[Callable[[List[Chunk]], List[Chunk]]] = chunk_by_page,
    local: bool = False,
    options: Optional[Dict[str, Any]] = None,
    openai_client: Optional[OpenAI] = None,
) -> List[Dict]:
    print(
        f"[thepipe] Extract functions will be deprecated in future versions. See the README for more information"
    )
    
    if local:
        chunks = scrape_url(
            url,
            text_only=text_only,
            ai_extraction=ai_extraction,
            verbose=verbose,
            local=local,
            chunking_method=chunking_method,
            options=options,
            openai_client=openai_client,
        )
        
        # Pass through options dictionary that might contain LLM config
        return extract(
            chunks=chunks,
            schema=schema,
            ai_model=ai_model,
            multiple_extractions=multiple_extractions,
            extraction_prompt=extraction_prompt,
            host_images=host_images,
            options=options,
            openai_client=openai_client,
        )[0]
    else:
        headers = {"Authorization": f"Bearer {THEPIPE_API_KEY}"}
        data = {
            "urls": [url],
            "schema": json.dumps(schema),
            "ai_model": ai_model,
            "multiple_extractions": str(multiple_extractions).lower(),
            "extraction_prompt": extraction_prompt,
            "host_images": str(host_images).lower(),
            "text_only": str(text_only).lower(),
            "ai_extraction": str(ai_extraction).lower(),
            "chunking_method": chunking_method.__name__,
            "options": json.dumps(options) if options else None
        }
        
        response = requests.post(
            f"{HOST_URL}/extract", headers=headers, data=data, stream=True
        )
        if response.status_code != 200:
            raise Exception(
                f"API request failed with status code {response.status_code}: {response.text}"
            )

        results = []
        for line in response.iter_lines(decode_unicode=True):
            if line:
                data = json.loads(line)
                if "extraction_complete" in data:
                    break
                result = data["result"]
                if "error" in result:
                    results.append(result)
                else:
                    extracted_data = {
                        "chunk_index": result["chunk_index"],
                        "source": result["source"],
                    }
                    if multiple_extractions:
                        extracted_data["extraction"] = result.get("extraction", [])
                    else:
                        extracted_data.update(result)
                        schema_keys = (
                            json.loads(schema).keys()
                            if isinstance(schema, str)
                            else schema.keys()
                        )
                        for key in schema_keys:
                            if key not in extracted_data:
                                extracted_data[key] = None
                    results.append(extracted_data)

        return results

def extract_from_file(
    file_path: str,
    schema: Union[str, Dict],
    ai_model: str = "google/gemma-2-9b-it",
    multiple_extractions: bool = False,
    extraction_prompt: str = DEFAULT_EXTRACTION_PROMPT,
    host_images: bool = False,
    text_only: bool = False,
    ai_extraction: bool = False,
    verbose: bool = False,
    chunking_method: Optional[Callable[[List[Chunk]], List[Chunk]]] = chunk_by_page,
    local: bool = False,
    options: Optional[Dict[str, Any]] = None,
    openai_client: Optional[OpenAI] = None,
) -> List[Dict]:
    print(
        f"[thepipe] Extract functions will be deprecated in future versions. See the README for more information"
    )
    
    if local:
        chunks = scrape_file(
            file_path,
            ai_extraction=ai_extraction,
            text_only=text_only,
            verbose=verbose,
            local=local,
            chunking_method=chunking_method,
            options=options,
            openai_client=openai_client,
        )
        
        # Pass through options dictionary that might contain LLM config
        return extract(
            chunks=chunks,
            schema=schema,
            ai_model=ai_model,
            multiple_extractions=multiple_extractions,
            extraction_prompt=extraction_prompt,
            host_images=host_images,
            options=options,
            openai_client=openai_client,
        )[0]
    else:
        headers = {"Authorization": f"Bearer {THEPIPE_API_KEY}"}
        data = {
            "schema": json.dumps(schema),
            "ai_model": ai_model,
            "multiple_extractions": str(multiple_extractions).lower(),
            "extraction_prompt": extraction_prompt,
            "host_images": str(host_images).lower(),
            "text_only": str(text_only).lower(),
            "ai_extraction": str(ai_extraction).lower(),
            "chunking_method": chunking_method.__name__,
            "options": json.dumps(options) if options else None
        }
        files = {"files": (os.path.basename(file_path), open(file_path, "rb"))}

        response = requests.post(
            f"{HOST_URL}/extract", headers=headers, data=data, files=files, stream=True
        )
        if response.status_code != 200:
            raise Exception(
                f"API request failed with status code {response.status_code}: {response.text}"
            )

        results = []
        for line in response.iter_lines(decode_unicode=True):
            if line:
                data = json.loads(line)
                if "extraction_complete" in data:
                    break
                result = data["result"]
                if "error" in result:
                    results.append(result)
                else:
                    extracted_data = {
                        "chunk_index": result["chunk_index"],
                        "source": result["source"],
                    }
                    if multiple_extractions:
                        extracted_data["extraction"] = result.get("extraction", [])
                    else:
                        extracted_data.update(result)
                        schema_keys = (
                            json.loads(schema).keys()
                            if isinstance(schema, str)
                            else schema.keys()
                        )
                        for key in schema_keys:
                            if key not in extracted_data:
                                extracted_data[key] = None
                    results.append(extracted_data)

        return results