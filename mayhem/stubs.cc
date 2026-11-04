// Symbols ya generates or the bundled libcxx provides, that this offline (libstdc++) build must supply.
extern "C" const int TStringUseCow = 0;  // libstdc++ std::string is not copy-on-write
extern "C" const char* GetArcadiaSourcePath() { return "/mayhem"; }
