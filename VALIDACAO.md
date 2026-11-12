# Validação — SABERLOCAL 2.0 / TX9

Esta lista registra o que ainda deve ser confirmado no equipamento físico.
Testes em um PC não validam o driver Wi-Fi da TX9.

## Verificações executadas no desenvolvimento

- 42 testes automatizados locais: backends Alpine/OpenRC e Ubuntu/systemd,
  renderização de serviços, proteção por UUID, limites FAT32, adaptadores do visor,
  portal, busca FTS sem acentos, filtro por disciplina,
  importação de texto, download do original, escape de HTML, geração de links
  Kiwix e resposta a detecção de portal.
- Downloads simulados: SHA-256, retomada Range, reinício quando o servidor ignora
  Range, corrupção, interrupção, orçamento e falta de espaço.
- Interface em navegador real (Edge), larguras de 1366 e 390 pixels: pesquisar,
  abrir uma atividade, conferir ausência de rolagem horizontal e layout de leitura.
- URLs, tamanhos e checksums das cinco coleções consultados no servidor oficial.
  Os livros completos não foram baixados no computador de desenvolvimento.
- A ativação de systemd/hostapd/DHCP não foi executada no Windows de desenvolvimento.
- A integração Linux no CI é separada dos testes de hardware: instala pacotes
  reais e verifica configurações/WSGI, mas não possui rádio TX9 ou microSD.

### Integração Linux executada

[Execução aprovada no GitHub Actions](https://github.com/joaopereiralabs/saberlocal/actions/runs/43327910523):

- Alpine **3.24.4 aarch64** e x86_64 em contêineres: pacotes reais de Kiwix,
  nginx, dnsmasq, Avahi, Flask e Waitress; configurações aceitas; HTTP na porta 80
  e consulta DNS A em interface fictícia; início do WSGI por OpenRC e recuperação
  após encerrar o processo filho.
- Ubuntu 24.04 x86_64 em contêiner: mesmas verificações de software/HTTP/DNS,
  mais `systemd-analyze verify` das units. O boot real por systemd não foi simulado.
- Importação de um PDF real via Poppler, com texto pesquisável e limites de
  memória/arquivo no subprocesso, nas três integrações Linux.

Esses testes usam rede isolada e uma interface `dummy`, sem rádio, DHCP de um
celular ou cartão físico. Alpine 3.24.2 foi informado pela equipe; a imagem de CI
disponível usa o patch 3.24.4. Não declarar como teste físico da TX9.

## Evidência informada pela equipe

Hotspot hostapd, DHCP dnsmasq e portal em `http://10.42.0.1:8090` funcionaram
manualmente em Alpine na TX9, com um celular e sem internet externa. Esse resultado
foi relatado pelo operador; não foi reproduzido pelo Codex em hardware próprio.

## Pendências na TV Box

1. Confirmar Alpine persistente, OpenRC, arquitetura aarch64 e `iw phy` com modo AP.
2. Conferir cartão UUID `21B5-12FD` e dados existentes antes da instalação.
3. Instalar por Ethernet com `sudo bash install.sh`; observar a montagem automática,
   os downloads e a migração dos processos manuais conhecidos.
4. Confirmar `sudo saberlocal status`, página inicial, cinco coleções e `/healthz`.
5. Desconectar a Ethernet e ligar um celular ao Wi-Fi. Conferir IP via DHCP,
   abertura por HTTP e pesquisa real de um termo em cada coleção.
6. Testar abertura automática em Android e iOS; registrar onde o endereço manual foi necessário.
7. Importar uma apostila PDF com texto, pesquisar uma palavra do seu interior e baixar o original.
8. Testar com 3–5 clientes, medir memória com `free -m` e observar tempos de busca.
   Aumentar a carga gradualmente; registrar o limite adequado à escola.
9. Fazer shutdown, remover a alimentação, religar sem monitor/teclado e sem
   Ethernet: rede, DNS/DHCP, portal, materiais e livros devem voltar sem intervenção.
10. Desligar corretamente, retirar o cartão e iniciar: os serviços devem pular
    a inicialização, sem gravar o acervo na eMMC por engano. Recolocar e reiniciar.
11. Confirmar que SSH não está acessível pelo Wi-Fi dos alunos, permanecendo
    acessível pelo canal administrativo planejado.
12. Encerrar o processo do portal e verificar recuperação pelo supervisor.
    Provocar falhas sucessivas em bancada e confirmar que o limite de tentativas é respeitado.
13. Avaliar `saberlocal-display probe`; somente com API confirmada, testar os estados
    boot/ready/error. Sem driver, confirmar estado unsupported sem afetar o servidor.
14. Em bancada, testar atualização fixada por commit, `rollback --app` e
    `sudo saberlocal rollback`, sem apagar conteúdos.
15. Repetir os testes de instalação/boot em Ubuntu/Armbian se essa plataforma
    também for utilizada pela escola.

Registre data, imagem do SO, kernel, driver, rádio utilizado, número de clientes
e resultados. Só depois desse teste considere a imagem apta para replicação nas escolas.
