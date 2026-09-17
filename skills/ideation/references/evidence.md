# 설계 근거와 효과의 한계

검토 기준일은 2026-09-17이다. 이 스킬은 TRIZ 원문을 번역·전재한 교재가 아니라, 공개 원전에서 얻은 사고 동작과 실증 연구의 한계를 반영한 개인 작업 지침이다. 원전의 절차 설명과 이 스킬의 효과는 별개다.

## 원전과 적용 범위

- [Altshuller의 ARIZ-85V](https://altshuller.ru/triz/ariz85v.asp): 도입·1~9부와 표1·표2를 검토했다. 단순 원리 선택을 넘어 문제 모델·자원·기술화·검증을 다룬다.
- [76표준 전문](https://www.altshuller.ru/triz/standards.asp), [40원리](https://www.altshuller.ru/world/eng/technique1.asp): 특정 기술 문제 모델에서 탐색을 돕는다. 표준 번호와 행렬은 일반 업무의 자동 답변기가 아니다.
- [시스템 사고](https://altshuller.ru/triz/triz70.asp), [RVS](https://altshuller.ru/triz/triz20.asp), [판타그램](https://www.altshuller.ru/triz/triz9.asp): 문제 수준·제약·상상력의 변환을 다룬다. 상상력 훈련의 결과를 실제 해법으로 채택하려면 현실 조건으로 돌아와야 한다.
- [MATRIZ 도구 체계](https://wiki.matriz.org/docs/triz/problem-solving-tools-5890/)와 각 도구 카드에 연결한 기능·인과·트리밍 문서: 후대의 분석 방법이다. 고전 단일 판본과 혼동하지 않는다.
- [Khomenko의 OTSM 개설](https://otsm-triz.org/sites/default/files/ready/khomenko050120supershortintroductionintoclassicaltrizandotsm.pdf): 연결된 비표준 문제를 다루는 후대 일반화다. 전문지식과 정량 평가를 대체하지 않는다.

원문의 공식·예제·훈련 과제 전체는 포함하지 않는다. 원문 링크가 공개돼 있다는 사실만으로 재배포 라이선스가 있다고 가정하지 않는다. 전체 TRIZ·RTV·OTSM 교재를 모두 구현한 스킬도 아니다.

## 실증 근거가 바꾸는 동작

| 근거 | 확인한 범위 | 이 스킬의 판단 |
|---|---|---|
| [Hernandez 외, 2013](https://doi.org/10.1115/1.4024976), [Okudan 외, 2006](https://doi.org/10.1115/DETC2006-99483) | 일부 공학 학생 과제에서 참신성·다양성·개념 수의 개선, 표본·과제·집단 통제의 한계 | 발상 보조로 시험하되 일반 업무의 효과로 확대하지 않음 |
| [Birdi 외, 2012](https://doi.org/10.1111/j.1467-9310.2012.00686.x), [Haines-Gadd, 2015](https://doi.org/10.1016/j.proeng.2015.12.387) | 아이디어 제안과 현장 구현·성과의 결과가 같지 않음. Birdi는 초록 범위 확인 | 아이디어의 매력과 구현 후 효과를 구별 |
| [AutoTRIZ](https://arxiv.org/abs/2403.13002), [TRIZ-GPT](https://arxiv.org/abs/2408.05897) | 교재 모순 복원·TRIZ 내부 프롬프트·해법 유사성 중심 | 절차 재현을 창의성·실용성의 증명으로 취급하지 않음 |
| [TRIZ Agents](https://arxiv.org/abs/2506.18783), [TRIZBENCH](https://aclanthology.org/2026.findings-acl.1798/) | 전자는 단일 공학 사례의 다중 역할 데모. 후자는 모순·원리·근거 하위 과제 평가 | 역할 수를 기본으로 늘리지 않으며 원리 검색과 최종 해법 품질을 구분 |
| [Doshi·Hauser, 2024](https://doi.org/10.1126/sciadv.adn5290), [Anderson 외, 2024](https://arxiv.org/abs/2402.01536) | 일부 과제에서 개인 산출 향상과 집단 동질화가 함께 관측 | 후보 수와 실제 작동 방식의 다양성을 구별 |
| [Si 외, ICLR 2025](https://arxiv.org/abs/2409.04109) | NLP 연구 아이디어의 익명 전문가 평가, 대량 생성의 중복과 자체 순위 한계 | 유명한 첫 답과 표현만 바뀐 후보에 머물지 않고, 채택 평가를 자기 점수만으로 끝내지 않음 |
| [Wadinambiarachchi 외, CHI 2024](https://arxiv.org/abs/2403.11164) | 시각 설계에서 생성 이미지에 의한 고착 관측. 텍스트 일반화는 미검증 | 첫 예시를 정답으로 고정하지 않고 다른 구조·전제를 비교 |
| [Hubert 외, 2024](https://doi.org/10.1038/s41598-024-53303-w), [Kumar 외, 2025](https://aclanthology.org/2025.emnlp-main.1704/) | 발산 지표와 실제 유용성의 차이, 배경 근거와 아이디어 평가의 한계 | 참신성·사실성·실현 가능성·실제 효과를 분리 |
| [Wang 외, 2024](https://aclanthology.org/2024.emnlp-main.1112/), [Tran·Kiela, 2026 사전공개본](https://arxiv.org/abs/2604.02460) | 정답형 추론에서 총 예산을 맞춘 전략 비교. 창의성 직접 증거는 아님 | 추가 추론·역할의 효과를 비교할 때 총 비용과 호출을 포함 |

빠른 공유에서 답을 먼저 하는 규칙은 사용자 요구와 기존 공유·회신 절차에서 온다. 위 이야기·발산 실험이 그 전달 순서를 입증한 것은 아니다. 고정 후보 수·재발산 횟수·특정 모드의 성능 우위를 문헌이 검증했다고 주장하지 않는다.

## 검증할 때

같은 모델·과제·근거·도구·생성 설정에서 직접 요청, 구조화 발상, 이 스킬을 비교한다. 빠른 모드는 실제 전달 계약과 응답 부담, 고찰 모드는 서로 다른 기제·제약 충족·검증 가능한 대안을 평가한다. 스킬 자체를 고치려는 평가에는 작성과 분리된 판정과 가능한 실제 실행을 사용한다.

일반 사용 중 매번 외부 검토자나 승인을 요구하는 절차는 아니다. 스킬의 품질 우위를 주장할 때 필요한 증거의 기준이다. 파일럿 산출물, LLM 판정, 사람이 측정한 효과, 실제 구현 결과는 별도로 보고한다.
