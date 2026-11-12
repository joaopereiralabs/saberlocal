# SABERLOCAL 2.0 — escola offline na TV Box TX9

Biblioteca para **ensino fundamental e médio**, acessível pelo Wi-Fi da TV Box,
sem internet durante o uso. O mesmo instalador seleciona **Alpine/OpenRC** ou
**Ubuntu/Debian/systemd**. Não exige Docker, interface gráfica ou IA.

Esta versão automatiza a instalação e os serviços. A configuração manual de
hotspot + DHCP + portal no Alpine foi comprovada pela equipe do projeto. Isso
não comprova cold boot, o acervo completo, o visor ou atendimento a uma turma.
Essas verificações estão em [VALIDACAO.md](VALIDACAO.md).

## Instalação no Alpine

Com o Alpine persistente preparado, o cartão inserido e internet por **Ethernet**:

```sh
sudo apk update
sudo apk add git bash
git clone https://github.com/joaopereiralabs/saberlocal.git
cd saberlocal
sudo bash install.sh
```

Enquanto esta alteração estiver em pull request, use o ramo de teste depois do clone:

```sh
git switch feat/alpine-openrc-tx9
sudo bash install.sh
```

O instalador detecta a distribuição, versão, arquitetura, OpenRC ativo e rádio
com modo AP; instala os pacotes; prepara um ambiente virtual; identifica e monta
o microSD; baixa/verifica o acervo; configura rede e serviços; habilita o início
automático e verifica o portal. Não é necessário editar IPs ou arquivos de serviço.

O rádio será dedicado ao projeto. Instale por Ethernet ou console, não por uma
conexão SSH que dependa desse mesmo rádio. A Ethernet não é reconfigurada.

### Valores da TX9 deste projeto

| Item | Configuração |
|---|---|
| Sistema informado pela equipe | Alpine Linux 3.24.2, aarch64 |
| Serviços | OpenRC, confirmado no momento da instalação |
| RAM / armazenamento interno | aproximadamente 1 GB / 7,3 GiB |
| Dados | microSD de aproximadamente 29,73 GiB, FAT32 |
| UUID padrão | `21B5-12FD` |
| Montagem / acervo | `/mnt/saberlocal` / `/mnt/saberlocal/acervo` |
| Rádio preferido | `wlan0`, somente se anunciar AP |
| SSID | `SABERLOCAL` |
| Senha | individual, gerada e preservada nas reinstalações |
| Endereços | **http://saber.local** e **http://10.42.0.1** |

O nome `/dev/mmcblk0p1` não é usado como identidade. O instalador verifica UUID
único, tipo de dispositivo, formato, tamanho e se a partição corresponde à raiz.
Em outro aparelho/cartão, informe o UUID real:

```sh
lsblk -f
sudo bash install.sh --storage-uuid OUTRO-UUID
```

Não há formatação, alteração de DTB, firmware, bootloader, kernel ou driver Wi-Fi.
Alpine iniciado com raiz em `tmpfs`/`ramfs` (modo diskless) não é suportado para
esta promessa de persistência: prepare instalação em modo `sys`. Não é feito um
`lbu commit` ou cache de pacotes implícito.

### Instalar sem coleções ou apenas preparar

```sh
sudo bash install.sh --no-content
sudo bash install.sh --no-content --no-activate
sudo bash install.sh --check --interface wlan0
```

`--no-content` mantém os guias/atividade iniciais e permite importar apostilas.
`--no-activate` instala pacotes, monta o cartão e grava configurações, mas não
inicia nem habilita os serviços novos. Para ativar depois: `sudo saberlocal restart`.
`--check` é leitura: exige as ferramentas de diagnóstico já instaladas e não
instala pacotes, monta cartão ou grava configurações.

Se Kiwix não estiver disponível para a combinação de versão/repositórios/arquitetura,
o instalador informa a falha; não baixa executável glibc incompatível com musl.
É possível preparar explicitamente só o portal:

```sh
sudo bash install.sh --no-kiwix --no-content
```

Nesse caso, o resumo não declara a biblioteca ZIM pronta.

## O que inicia depois de ligar

```text
Alpine/OpenRC ou Ubuntu/systemd
  └─ storage: espera o cartão por UUID, monta e confere
      ├─ network: espera o rádio, aplica firewall e IP
      │   ├─ hostapd: Wi-Fi WPA2
      │   ├─ dns: DHCP e DNS local
      │   └─ mdns: anuncia saber.local
      ├─ portal: Waitress + Flask, usuário saberlocal, loopback:8090
      └─ kiwix: livros ZIM, loopback:8081, quando houver acervo
          web: nginx, porta 80, depende de rede, portal, hotspot e DHCP
          display: opcional, sem bloquear os demais
```

O boot e a operação não fazem downloads. OpenRC usa `supervise-daemon`, intervalo
de 5 s e no máximo 5 respawns durante a vida do supervisor. Systemd tem limite
de 5 partidas em 300 s e intervalo de 10 s. Esgotado o limite, o componente fica
parado para diagnóstico; não existe temporizador que o reinicie para sempre.
`sudo saberlocal restart` reinicia os componentes depois da correção.

Logs persistentes ficam em `/var/log/saberlocal`, com logrotate a cada 15 minutos,
limite de rotação de 1 MiB por arquivo e três cópias. Não há logs de acesso HTTP,
analytics, conta de aluno ou histórico de pesquisas. DHCP e diagnósticos podem
conter identificadores de rede. Logs podem crescer entre duas execuções da rotação.

Não há garantia de quantidade de alunos simultâneos: o teto de 20 estações no
hostapd não equivale a capacidade validada. Comece com 3–5 clientes na bancada.
Waitress usa duas threads e conexões limitadas; Kiwix usa duas threads. No systemd
também há limites de memória. OpenRC supervisiona processos, sem alegar um limite
de RAM que dependa de cgroups não verificados no Alpine.

## Cartão FAT32 e segurança dos dados

O instalador cria uma única seção identificada no `/etc/fstab`, usando UUID,
`noauto,nosuid,nodev,noexec`; o serviço de armazenamento faz a montagem com espera
limitada. `noauto` evita uma espera indefinida do boot por cartão ausente. Uma
montagem já declarada pelo operador no mesmo UUID/ponto é reutilizada sem duplicação.
Entradas conflitantes são recusadas, sem apagar configurações alheias.

No FAT32, são aplicadas permissões de leitura para o usuário do portal, escrita
administrativa e nenhuma execução. A política também é aplicada depois do boot.
Não se utiliza `chmod -R` em todo o cartão. Livros, PDFs, catálogo SQLite e textos
extraídos ficam no cartão; o código/ambiente virtual pequenos ficam na raiz.
Python e bibliotecas são instalados por pacotes da distribuição, sem compilação
pip ou duas cópias completas de suas dependências.

Cada ZIM é verificado por tamanho antes da transferência. FAT32 aceita no máximo
**4 GiB menos 1 byte por arquivo**. Arquivos maiores são recusados antes do download:
escolha coleções menores ou prepare outro cartão ext4 externamente. Nenhum comando
do instalador oferece formatação. O acervo inicial cabe nesse limite por arquivo.

A instalação e a administração verificam UUID e montagem; os downloads repetem
a verificação durante a transferência. Sem cartão, não é criado um acervo
substituto na eMMC. O portal responde com mensagem de indisponibilidade se detectar
a remoção. Remova o cartão somente com o equipamento desligado corretamente:
FAT32 e SQLite não eliminam risco de corrupção por corte de energia. O desligamento
total deve ocorrer após o shutdown do sistema, quando possível.

Para trocar por cartão maior, copie o acervo com o equipamento parado e reinstale
com `--storage-uuid NOVO-UUID`. Ajuste `max_content_gib` se quiser mais de 20 GiB.

## Acesso no celular

Conecte ao Wi-Fi com a senha exibida ao instalar. Aceite permanecer na rede sem
internet. Use **http://saber.local** ou **http://10.42.0.1**, sem `:8090`.

DNS local e Avahi/mDNS atendem `saber.local`. O domínio `.local` recebe tratamento
especial em alguns sistemas; o IP é a alternativa mais confiável. Android/iOS
podem abrir o portal ao detectar a rede. Não é garantido em todos os aparelhos.
Não se intercepta HTTPS; VPN, DNS privado e troca automática para dados móveis
podem exigir ajuste temporário no celular.

A administração por SSH é bloqueada no Wi-Fi dos alunos, preservada na Ethernet.
O firewall bloqueia encaminhamento entre redes. Regras alheias não são limpas;
um firewall externo pode exigir permissões adicionais para TCP 80/53, UDP 53/67
e mDNS UDP 5353 na interface escolhida. O isolamento entre celulares depende do driver.

## Biblioteca e materiais dos professores

As cinco coleções fixadas em `content/packs.json` são matemática, física,
geografia, história e Wikilivros em português. Ocupam aproximadamente 1,18 GiB.
Os ZIM são lidos compactados. A pesquisa de apostilas usa SQLite FTS5, sem acentos;
a pesquisa nas coleções é feita pelo Kiwix, pelo botão da disciplina.

```sh
sudo saberlocal import /caminho/atividade.pdf \
  --title 'Matemática — 7º ano — Frações' \
  --category 'Matemática' \
  --source 'Autoria real do material' \
  --license 'Permissão real de uso' \
  --date '2026-11-12'
```

PDF com texto, TXT e Markdown são aceitos (100 MiB de arquivo, 5 MiB de texto).
PDF escaneado precisa de OCR prévio. O original pode ser baixado. Os professores
selecionam materiais adequados às turmas; os acervos públicos não são uma sequência
didática nem certificam alinhamento à BNCC. Importe somente conteúdo autorizado,
sem dados pessoais de estudantes.

Downloads interrompidos são retomados por Range, ou reiniciados se o servidor
ignorar Range. SHA-256 é validado antes de publicar o arquivo. Há orçamento padrão
de 20 GiB e reserva de 512 MiB no cartão. Não se apagam edições antigas automaticamente.

## Administração, atualização e recuperação

```sh
sudo saberlocal status
sudo saberlocal logs
sudo saberlocal restart
sudo saberlocal download
sudo saberlocal update --ref SHA_COMPLETO_DE_40_CARACTERES
sudo saberlocal rollback --app
sudo saberlocal rollback
```

`status` mostra os componentes reais, montagem/UUID/espaço, número de ZIMs,
autostart e estado do visor; não imprime a senha. `download` completa/verifica o
manifesto instalado, habilita e reinicia Kiwix. Não escolhe uma edição nova sem
alterar o manifesto. `logs` apresenta o final dos arquivos de diagnóstico.

`update --ref` obtém exatamente o commit escolhido do repositório oficial,
prepara uma nova versão, valida o ambiente Python e executa o instalador sem
reinstalar pacotes ou baixar coleções. Preserva senha, configuração e conteúdos.
Internet é necessária só nesse comando. Para atualização com repositório Git local:

```sh
sudo saberlocal update --ref SHA_COMPLETO --source /caminho/repo-git-local
```

Se a nova versão exigir novos pacotes do SO, execute o instalador normalmente
com internet. Não há atualização automática em segundo plano. Cópias do código
ficam em `/opt/saberlocal/releases`, com `current` apontando para a versão ativa.
Diretórios temporários de atualização são preservados para diagnóstico e podem
ser removidos pelo responsável quando não forem necessários.

`rollback --app` volta ao código e configurações da versão anterior registrada,
mantendo o cartão. `rollback` desativa os serviços e restaura os arquivos anteriores
à instalação 2.0, sem apagar acervo, pacotes ou código. Alterações externas feitas
depois da instalação são detectadas e preservadas: nesse caso, a restauração é
recusada até revisão. A seção de fstab do projeto é removida isoladamente, sem
apagar entradas acrescentadas por outras pessoas.

Não edite os arquivos gerados em `/etc/saberlocal`: para personalizar, copie
`config.example.json` para `config.local.json` (ignorado pelo Git), ajuste os
campos desejados e execute `sudo bash install.sh --config config.local.json`.
A senha privada fica em `/etc/saberlocal/private.json`, modo 0600. O padrão
provisório `SaberLocal2026` é substituído por senha individual.

Processos manuais são migrados automaticamente **somente** se corresponderem aos
comandos conhecidos (`hostapd /tmp/hostapd.conf`, `dnsmasq --conf-file=/tmp/dnsmasq.conf`
com interface/SSID correspondentes e `scripts/dev.py` neste checkout). Não se usa
`pkill` genérico. Portas ocupadas por serviços externos são recusadas, preservando-os.
Processos manuais antigos não são ressuscitados pelo rollback.

## Visor frontal

```sh
sudo saberlocal-display probe
```

Esse comando lista candidatos em `/sys`, `/dev`, módulos e eventual `tx9-show`.
A presença de um dispositivo ou nome não confirma API de texto. O padrão é
`display_backend: "none"`: estado **unsupported**, sem bloquear a escola.

Há adaptadores reais para comando com argumentos (sem shell, timeout de 3 s) e
atributo sysfs de texto documentado. Depois de confirmar a API do driver instalado,
configure um adaptador em `config.local.json`, por exemplo:

```json
{
  "display_backend": "command",
  "display_command": ["/caminho/absoluto/adaptador-confirmado", "{text}"],
  "display_width": 4
}
```

É um contrato de integração, não um driver incluído nem um comando que se presume
existir. Depois de reinstalar com essa configuração, o serviço tenta `boot`,
`SAbE` quando pronto ou `Err` em falha. Com dois caracteres usa `SL`; com largura
suficiente usa `SaberLocal`. O texto e a disponibilidade efetiva dependem do driver.
Falhas são registradas; não bloqueiam os demais serviços.

Referência histórica: [rbeninca/tvbox](https://github.com/rbeninca/tvbox). O projeto
não redistribui seus drivers nem escreve `/dev/mem`, registradores GPIO ou nós de
dispositivo desconhecidos. A TX9 não foi acessada remotamente nesta implementação.

## Ubuntu/Debian/Armbian e desenvolvimento

Ubuntu/Debian com systemd continuam usando `sudo bash install.sh`; o instalador
seleciona `apt-get` e gera units próprias. `--storage-uuid` identifica o cartão
de cada unidade. O ambiente de produção usa Waitress nas duas plataformas.

Em um computador de desenvolvimento, sem montar cartão nem configurar Wi-Fi:

```sh
python -m pip install -r requirements.txt
python scripts/dev.py
python -m unittest discover -s tests -v
```

A demonstração fica restrita a `127.0.0.1:8090`; a produção usa nginx na porta 80.
Os testes incluem backends separados, configuração, proteção do cartão, FAT32,
busca, importação, downloads e adaptadores do visor. CI também usa contêineres
descartáveis Alpine x86_64/ARM64 e Ubuntu para conferir pacotes, sintaxe dos
daemons e WSGI. Docker é ferramenta de CI, **não requisito da TV Box**.

Consulte [VALIDACAO.md](VALIDACAO.md) para resultados e pendências de bancada e
[NOTICE.md](NOTICE.md) para fontes e licenças.
