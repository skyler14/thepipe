#ifndef THEPIPE_CODEGRAPH_H
#define THEPIPE_CODEGRAPH_H

#if defined(_WIN32)
#define TP_EXPORT __declspec(dllexport)
#else
#define TP_EXPORT __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct tp_context tp_context;
typedef struct tp_store tp_store;

TP_EXPORT tp_context *tp_context_new(const char *cache_dir);
TP_EXPORT const char *tp_abi_version(void);
TP_EXPORT int tp_context_set_quiet(tp_context *context, int quiet);
TP_EXPORT int tp_context_call(tp_context *context, const char *tool,
                              const char *request_json, char **out_json);
TP_EXPORT void tp_context_free(tp_context *context);
TP_EXPORT tp_store *tp_store_open_query(const char *db_path);
TP_EXPORT int tp_store_call(tp_store *store, const char *action,
                            const char *request_json, char **out_json);
TP_EXPORT int tp_cypher_query(tp_store *store, const char *request_json, char **out_json);
TP_EXPORT void tp_store_close(tp_store *store);
TP_EXPORT const char *tp_version(void);
TP_EXPORT void tp_string_free(char *value);

#ifdef __cplusplus
}
#endif

#endif
