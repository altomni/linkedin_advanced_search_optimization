#!/usr/bin/env python3
"""
LinkedIn完整集成服务
包含LinkedIn Recruiter爬虫API和Profile查询API的完整工作流
"""
import os

import requests
import logging
import base64
import time
from typing import Dict, List, Optional, Union

from dotenv import load_dotenv

# 设置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

load_dotenv()


class LinkedInIntegrationService:
    """LinkedIn完整集成服务 - 包含爬虫和查询功能"""

    def __init__(
        self,
        recruiter_api_base_url: str = "http://34.208.174.174:3022",
        profile_api_base_url: Optional[str] = None,
        profile_api_key: str = "aisourcing2025",
    ):
        """
        初始化LinkedIn集成服务

        Args:
            recruiter_api_base_url: LinkedIn Recruiter API基础URL
            profile_api_base_url: Profile查询API基础URL（默认取 LINKEDIN_SEARCH_BASE_IP:5678，
                该变量属于 navigator/profile 爬虫路径，未配置时为 None）
            profile_api_key: Profile查询API的认证密钥
        """
        if profile_api_base_url is None:
            base_ip = os.environ.get("LINKEDIN_SEARCH_BASE_IP")
            profile_api_base_url = f"{base_ip}:5678" if base_ip else None

        self.recruiter_base_url = recruiter_api_base_url
        self.profile_base_url: Optional[str] = profile_api_base_url
        self.profile_api_key = profile_api_key

        # 生成Bearer Token
        self.profile_token = base64.b64encode(profile_api_key.encode()).decode()
        self.profile_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.profile_token}",
        }

        logger.info(f"初始化LinkedIn集成服务:")
        logger.info(f"  - Recruiter API: {self.recruiter_base_url}")
        logger.info(f"  - Profile API: {self.profile_base_url}")
        logger.info(f"  - Bearer Token: {self.profile_token}")

    # ==================== LinkedIn Recruiter 爬虫 API ====================

    def trigger_crawl_task(self, linkedin_ids: List[str]) -> Dict:
        """
        发布批量LinkedIn爬虫任务

        Args:
            linkedin_ids: LinkedIn ID列表

        Returns:
            Dict: 包含task_uuid等信息的响应数据

        Example:
            >>> service = LinkedInIntegrationService()
            >>> result = service.trigger_crawl_task([
            ...     "ACwAAEBKRW4BzsP-dbwd3qxUNJwu9QSvjzuGKXw",
            ...     "ACwAADt745EBiJ22qF25FTcyL0jyxcWGHM9lVSE"
            ... ])
            >>> print(result['task_uuid'])
        """
        try:
            url = f"{self.recruiter_base_url}/priorityLnkd"
            payload = {"linkedin_ids": linkedin_ids}

            logger.info(f"发布爬虫任务: {len(linkedin_ids)} 个LinkedIn ID")
            response = requests.post(url, json=payload, timeout=30)

            if response.status_code != 200:
                raise Exception(
                    f"发布任务失败: HTTP {response.status_code} - {response.text}"
                )

            data = response.json()
            if data.get("status") != "success":
                raise Exception(f"发布任务失败: {data}")

            task_uuid = data.get("task_uuid")
            logger.info(f"✅ 成功发布爬虫任务: {task_uuid}")
            return data

        except requests.exceptions.Timeout:
            raise Exception("发布任务超时")
        except requests.exceptions.RequestException as e:
            raise Exception(f"发布任务网络错误: {str(e)}")
        except Exception as e:
            raise Exception(f"发布任务失败: {str(e)}")

    def get_crawl_status(self, task_uuid: str) -> Dict:
        """
        查询爬虫任务状态

        Args:
            task_uuid: 任务UUID

        Returns:
            Dict: 任务状态信息

        Example:
            >>> status = service.get_crawl_status("48a3cd23-d994-44e4-944f-012df413ce29")
            >>> print(status['status'])  # "processing" 或 "completed"
        """
        try:
            url = f"{self.recruiter_base_url}/priorityLnkd/status/{task_uuid}"

            logger.debug(f"查询爬虫状态: {task_uuid}")
            response = requests.get(url, timeout=30)

            try:
                data = response.json()
                if "status" not in data:
                    raise Exception(f"无效的状态响应: {response.text}")

                status = data.get("status")
                logger.debug(f"任务状态: {status}")
                return data

            except Exception as e:
                if response.status_code != 200:
                    raise Exception(
                        f"状态查询失败: HTTP {response.status_code} - {response.text}"
                    )
                else:
                    raise Exception(f"解析状态响应失败: {str(e)}")

        except requests.exceptions.Timeout:
            raise Exception("状态查询超时")
        except requests.exceptions.RequestException as e:
            raise Exception(f"状态查询网络错误: {str(e)}")
        except Exception as e:
            raise Exception(f"状态查询失败: {str(e)}")

    def trigger_manual_processing(self) -> Dict:
        """
        手动触发任务处理（插队）

        Returns:
            Dict: 触发结果信息

        Example:
            >>> result = service.trigger_manual_processing()
            >>> print(result['status'])  # "success"
        """
        try:
            url = f"{self.recruiter_base_url}/tasks/priority_linkedin_consume/trigger"

            logger.info("触发手动任务处理")
            response = requests.get(url, timeout=300)  # 固定5分钟超时

            if response.status_code != 200:
                raise Exception(
                    f"手动触发失败: HTTP {response.status_code} - {response.text}"
                )

            data = response.json()
            if data.get("status") == "success":
                result = data.get("result", {})
                processed_count = result.get("processed_count", 0)

                if processed_count > 0:
                    logger.info(f"✅ 手动触发成功，处理了 {processed_count} 个任务")
                else:
                    logger.info("✅ 手动触发成功，但队列为空")

                return data
            else:
                raise Exception(f"手动触发失败: {data}")

        except requests.exceptions.Timeout:
            raise Exception("手动触发超时（5分钟）")
        except requests.exceptions.RequestException as e:
            raise Exception(f"手动触发网络错误: {str(e)}")
        except Exception as e:
            raise Exception(f"手动触发失败: {str(e)}")

    # ==================== Profile 查询 API ====================

    def get_profile_by_task_uuid(self, task_uuid: str) -> Dict:
        """
        根据单个task_uuid查询profiles

        Args:
            task_uuid: 任务UUID

        Returns:
            Dict: 包含profiles的结构化数据

        Example:
            >>> profiles = service.get_profile_by_task_uuid("938df88f-c85e-4786-9bd0-e025ab7b4fde")
            >>> print(f"找到 {profiles['data']['profile_count']} 个profiles")
        """
        try:
            url = f"{self.profile_base_url}/profile/{task_uuid}"

            logger.info(f"查询单个task_uuid的profiles: {task_uuid}")
            response = requests.get(url, headers=self.profile_headers, timeout=30)

            if response.status_code != 200:
                raise Exception(
                    f"Profile查询失败: HTTP {response.status_code} - {response.text}"
                )

            data = response.json()
            if not data.get("success"):
                raise Exception(
                    f"Profile查询失败: {data.get('message', 'Unknown error')}"
                )

            profile_count = data["data"]["profile_count"]
            logger.info(f"✅ 成功查询到 {profile_count} 个profiles")
            return data

        except requests.exceptions.Timeout:
            raise Exception("Profile查询超时")
        except requests.exceptions.RequestException as e:
            raise Exception(f"Profile查询网络错误: {str(e)}")
        except Exception as e:
            raise Exception(f"Profile查询失败: {str(e)}")

    def get_profiles_by_task_uuids(self, task_uuids: List[str]) -> Dict:
        """
        根据多个task_uuid批量查询profiles

        Args:
            task_uuids: 任务UUID列表

        Returns:
            Dict: 包含所有task_uuids对应profiles的结构化数据

        Example:
            >>> profiles = service.get_profiles_by_task_uuids([
            ...     "3a25b6ab-2a8f-40ef-ba7f-e15e074da92c",
            ...     "938df88f-c85e-4786-9bd0-e025ab7b4fde"
            ... ])
            >>> print(f"总共找到 {profiles['data']['total_profile_count']} 个profiles")
        """
        try:
            url = f"{self.profile_base_url}/profiles"
            payload = {"task_uuids": task_uuids}

            logger.info(f"批量查询 {len(task_uuids)} 个task_uuids的profiles")
            response = requests.post(
                url, json=payload, headers=self.profile_headers, timeout=60
            )

            if response.status_code != 200:
                raise Exception(
                    f"批量Profile查询失败: HTTP {response.status_code} - {response.text}"
                )

            data = response.json()
            if not data.get("success"):
                raise Exception(
                    f"批量Profile查询失败: {data.get('message', 'Unknown error')}"
                )

            total_profiles = data["data"]["total_profile_count"]
            logger.info(f"✅ 成功批量查询到 {total_profiles} 个profiles")
            return data

        except requests.exceptions.Timeout:
            raise Exception("批量Profile查询超时")
        except requests.exceptions.RequestException as e:
            raise Exception(f"批量Profile查询网络错误: {str(e)}")
        except Exception as e:
            raise Exception(f"批量Profile查询失败: {str(e)}")

    def check_profile_api_health(self) -> Dict:
        """
        检查Profile API健康状态

        Returns:
            Dict: HealthCheck结果

        Example:
            >>> health = service.check_profile_api_health()
            >>> print(health['status'])  # "healthy"
        """
        try:
            url = f"{self.profile_base_url}/health"

            logger.debug("检查Profile API健康状态")
            response = requests.get(url, timeout=10)

            if response.status_code != 200:
                raise Exception(
                    f"健康检查失败: HTTP {response.status_code} - {response.text}"
                )

            data = response.json()
            if data.get("success") and data.get("status") == "healthy":
                logger.debug("✅ Profile API状态健康")
            else:
                logger.warning(f"⚠️ Profile API状态异常: {data}")

            return data

        except requests.exceptions.Timeout:
            raise Exception("健康检查超时")
        except requests.exceptions.RequestException as e:
            raise Exception(f"健康检查网络错误: {str(e)}")
        except Exception as e:
            raise Exception(f"健康检查失败: {str(e)}")

    # ==================== 完整工作流方法 ====================

    def crawl_and_query_workflow(
        self,
        linkedin_ids: List[str],
        max_wait_time: int = 7200,  # 默认2小时
        check_interval: int = 1,
        auto_trigger_after: int = 600,
    ) -> Dict:  # 10分钟后自动尝试手动触发
        """
        完整的爬虫+查询工作流

        Args:
            linkedin_ids: LinkedIn ID列表
            max_wait_time: 最大等待时间（秒），默认2小时，超过后自动退出
            check_interval: 状态检查间隔（秒），默认1秒
            auto_trigger_after: 自动尝试手动触发的等待时间（秒），默认10分钟

        Returns:
            Dict: 包含完整流程结果的数据

        Example:
            >>> result = service.crawl_and_query_workflow([
            ...     "ACwAAEBKRW4BzsP-dbwd3qxUNJwu9QSvjzuGKXw",
            ...     "ACwAADt745EBiJ22qF25FTcyL0jyxcWGHM9lVSE"
            ... ], max_wait_time=7200, auto_trigger_after=600)
            >>> profiles = result['profiles']
        """
        logger.info("🚀 开始完整的LinkedIn爬虫+查询工作流")

        # 步骤1: 发布爬虫任务
        logger.info("步骤1: 发布LinkedIn爬虫任务")
        crawl_result = self.trigger_crawl_task(linkedin_ids)
        task_uuid = crawl_result["task_uuid"]

        # 步骤2: 等待任务完成
        logger.info("步骤2: 等待爬虫任务完成")
        waited_time = 0
        manual_triggered = False

        while True:
            status_result = self.get_crawl_status(task_uuid)
            status = status_result.get("status")

            if status == "completed":
                logger.info("✅ 爬虫任务已完成")
                break
            elif status == "processing":
                logger.info(f"⏳ 任务处理中... 已等待 {waited_time} 秒")
            else:
                logger.warning(f"⚠️ 任务状态异常: {status}")

            # 检查是否需要手动触发
            if not manual_triggered and waited_time >= auto_trigger_after:
                logger.warning(
                    f"⚠️ 已等待 {auto_trigger_after} 秒，尝试手动触发任务处理"
                )
                try:
                    self.trigger_manual_processing()
                    manual_triggered = True
                    logger.info("✅ 手动触发成功，继续等待任务完成")
                except Exception as e:
                    logger.error(f"❌ 手动触发失败: {e}")
                    manual_triggered = True  # 标记为已尝试，避免重复触发

            # 检查是否超过最大等待时间
            if waited_time >= max_wait_time:
                logger.error(f"❌ 已等待超过最大时间 {max_wait_time} 秒，任务仍未完成")
                raise Exception(
                    f"任务等待超时，已等待 {max_wait_time} 秒，任务状态: {status}"
                )

            time.sleep(check_interval)
            waited_time += check_interval

        # 步骤3: 查询Profile结果
        logger.info("步骤3: 查询Profile结果")
        profiles_result = self.get_profile_by_task_uuid(task_uuid)

        # 整合结果
        final_result = {
            "crawl_task": crawl_result,
            "final_status": status_result,
            "profiles": profiles_result,
            "summary": {
                "task_uuid": task_uuid,
                "linkedin_ids_count": len(linkedin_ids),
                "profiles_found": profiles_result["data"]["profile_count"],
                "total_wait_time": waited_time,
                "manual_triggered": manual_triggered,
            },
        }

        logger.info(
            f"🎉 工作流完成! 处理了 {len(linkedin_ids)} 个LinkedIn ID，找到 {profiles_result['data']['profile_count']} 个profiles"
        )
        logger.info(
            f"📊 总等待时间: {waited_time} 秒，手动触发: {'是' if manual_triggered else '否'}"
        )
        return final_result


# 使用示例
if __name__ == "__main__":
    # 初始化服务
    service = LinkedInIntegrationService()

    # 测试数据
    test_linkedin_ids = [
        "ACwAAEBKRW4BzsP-dbwd3qxUNJwu9QSvjzuGKXw",
        "ACwAADt745EBiJ22qF25FTcyL0jyxcWGHM9lVSE",
    ]

    try:
        print("=== 测试LinkedIn集成服务 ===")

        # 测试1: 完整自动化工作流
        print("\n1. 完整自动化工作流测试...")
        result = service.crawl_and_query_workflow(
            test_linkedin_ids,
            max_wait_time=1800,  # 30分钟
            auto_trigger_after=300,  # 5分钟后尝试手动触发
        )
        print(f"工作流完成: 找到 {result['summary']['profiles_found']} 个profiles")
        print(f"等待时间: {result['summary']['total_wait_time']} 秒")
        print(f"手动触发: {'是' if result['summary']['manual_triggered'] else '否'}")

        # 测试2: 分步执行
        print("\n2. 分步执行测试...")

        # 发布任务
        crawl_result = service.trigger_crawl_task(test_linkedin_ids)
        task_uuid = crawl_result["task_uuid"]
        print(f"任务UUID: {task_uuid}")

        # 查询状态
        status = service.get_crawl_status(task_uuid)
        print(f"任务状态: {status['status']}")

        # 查询profiles（如果任务已完成）
        if status["status"] == "completed":
            profiles = service.get_profile_by_task_uuid(task_uuid)
            print(f"找到profiles: {profiles['data']['profile_count']}")

        # 测试3: 批量UUID查询测试
        print("\n3. 批量UUID查询测试...")
        bulk_task_uuids = [
            "dee4858b-82a4-4348-b400-279815958618",
            "4db0294f-50a6-454c-9452-07aa079ea47b",
        ]

        try:
            bulk_profiles = service.get_profiles_by_task_uuids(bulk_task_uuids)
            total_profiles = bulk_profiles["data"]["total_profile_count"]
            print(f"✅ 批量查询成功: 总共找到 {total_profiles} 个profiles")

            # 直接获取原始数据（1行代码）
            raw_profiles_data = bulk_profiles["data"]["task_results"]

            # 显示每个task_uuid的结果
            for task_result in bulk_profiles["data"]["task_results"]:
                task_uuid = task_result["task_uuid"]
                profile_count = task_result["profile_count"]
                print(f"   - {task_uuid}: {profile_count} 个profiles")

                if profile_count > 0:
                    # 显示第一个profile的简要信息
                    first_profile = task_result["profiles"][0]
                    name = first_profile.get("fullName", "N/A")
                    headline = first_profile.get("headline", "N/A")
                    print(f"     首个Profile: {name} - {headline}")

                    # 直接获取单个task_uuid的profiles原始数据（1行代码）
                    single_task_profiles = task_result["profiles"]

        except Exception as e:
            print(f"❌ 批量查询失败: {e}")

        # 测试4: 手动触发任务处理测试
        print("\n4. 手动触发任务处理测试...")

        try:
            trigger_result = service.trigger_manual_processing()
            if trigger_result.get("status") == "success":
                result_data = trigger_result.get("result", {})
                processed_count = result_data.get("processed_count", 0)
                processed_uuids = result_data.get("processed_task_uuids", [])

                print(f"✅ 手动触发成功")
                print(f"   处理任务数: {processed_count}")
                if processed_uuids:
                    print(f"   处理的UUID: {processed_uuids[:3]}...")  # 只显示前3个
                else:
                    print("   队列为空，无任务需要处理")
            else:
                print(f"❌ 手动触发失败: {trigger_result}")

        except Exception as e:
            print(f"❌ 手动触发测试失败: {e}")

        # 测试5: Profile API健康检查
        print("\n5. Profile API健康检查...")

        try:
            health = service.check_profile_api_health()
            if health.get("success") and health.get("status") == "healthy":
                print("✅ Profile API状态健康")
            else:
                print(f"⚠️ Profile API状态异常: {health}")

        except Exception as e:
            print(f"❌ 健康检查失败: {e}")

        print("\n✅ 所有测试完成")

    except Exception as e:
        print(f"❌ 测试失败: {e}")
