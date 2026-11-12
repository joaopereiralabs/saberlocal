# Histórico de versões

## 2.0.0 — 2026-11-12

- Instalador Alpine/OpenRC, mantendo Ubuntu/Debian/systemd.
- Inicialização automática dos serviços, supervisão com tentativas limitadas e logs com rotação.
- microSD identificado por UUID, montagem automática, suporte a FAT32 e proteção contra acervo na raiz sem cartão.
- Wi-Fi WPA2 com senha individual, DHCP/DNS, mDNS para saber.local e HTTP na porta 80.
- Portal de produção com Waitress, pesquisa local, importação de PDFs e integração com Kiwix.
- Administração, atualização fixada por commit e rollback com preservação do acervo.
- Visor opcional com diagnóstico e adaptadores explícitos; sem compatibilidade presumida.
- 42 testes automatizados e integração Linux em Alpine ARM64/x86_64 e Ubuntu x86_64.

Os testes de integração não substituem a validação física na TX9. Cold boot,
microSD físico, visor e capacidade com múltiplos celulares continuam pendentes
conforme [VALIDACAO.md](VALIDACAO.md). A imagem Alpine do CI é 3.24.4; a equipe
informou 3.24.2 no aparelho.
